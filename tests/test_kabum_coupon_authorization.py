"""Unidade comercial Awin e autorização local: SQLite temporário, rede proibida."""
import copy
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bot_ofertas_revisao as publisher
import cupons_kabum as coupons
from inteligencia_ofertas import Intelligence
from ofertas_core import Ledger
from prepublicacao import PrePublicationGate, GateReject
from revisao_publicacao import selection_id
from tests.test_cupons_kabum import voucher

NOW = datetime(2026, 10, 3, 13, tzinfo=timezone.utc)


class NativeFixture(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.moment = NOW
        self.stack.enter_context(patch('time.time', side_effect=lambda: self.moment.timestamp()))
        for module in ('prepublicacao', 'cupons_kabum', 'ofertas_core', 'bot_ofertas_revisao'):
            clock = self.stack.enter_context(patch(module + '.datetime', wraps=datetime))
            clock.now.side_effect = lambda tz=None: self.moment.astimezone(tz) if tz else self.moment.replace(tzinfo=None)
        self.network = self.stack.enter_context(patch('requests.sessions.Session.request', side_effect=AssertionError('real network forbidden')))
        self.ledger = Ledger(self.root / 'publicacoes.sqlite3')
        self.stack.callback(self.ledger.db.close)
        self.intelligence = Intelligence(self.ledger.db)
        self.affiliate = Mock()
        self.api = Mock(enabled=True)
        self.stack.enter_context(patch('awin_kabum.AwinKabumAPI.from_env', return_value=self.api))
        self.item = voucher()
        self.item.update(startDate=(NOW - timedelta(hours=1)).isoformat(), endDate=(NOW + timedelta(minutes=5)).isoformat())
        self.api.offers.return_value = [self.item]
        self.gate = PrePublicationGate(self.root, self.ledger.db, kabum_affiliate=self.affiliate)
        self.original, self.selected = self.enqueue(self.item)

    def enqueue(self, item):
        original = coupons.alert_from_offer(item, self.affiliate, NOW)
        self.intelligence.enqueue([original], now=NOW.timestamp())
        row = next(row for row in self.intelligence.pending(include_selection=True) if row['product_id'] == original['product_id'])
        selected = row.pop('_queue_selection')
        return row, selected

    def approve(self):
        prepared = coupons.prepare_alert(self.affiliate, self.original, NOW)
        return self.gate.validate(self.original, prepared, '@audit', selected=self.selected)

    def reserve(self, approved):
        day = self.ledger.reserve(approved['product_id'], offer=approved, channel='@audit', selected=self.selected)
        self.assertIsNotNone(day)
        return day

    def authorize(self, approved, day, ledger=None, proof_override=False, proof=None):
        public = dict(approved)
        extracted = public.pop('_kabum_coupon_proof', None)
        return (ledger or self.ledger).mark_sending(public['product_id'], day, selected=self.selected,
            approved_offer=public, kabum_coupon_proof=proof if proof_override else extracted)

    def reject(self, reason):
        with self.assertRaises(GateReject) as caught:
            self.approve()
        self.assertEqual(caught.exception.reason, reason)
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM posts').fetchone()[0], 0)
        self.network.assert_not_called()

    def changed(self, **fields):
        self.api.offers.return_value = [dict(self.item, **fields)]
        self.reject('REVISAO_COMERCIAL_ALTERADA')
        self.assertEqual(json.loads(self.ledger.db.execute('SELECT payload FROM radar_queue').fetchone()[0]), self.original)


class NativeGateTests(NativeFixture):
    def test_same_unit_active(self):
        approved = self.approve()
        self.assertTrue(self.authorize(approved, self.reserve(approved)))

    def test_shortened_period_is_new_revision(self):
        self.changed(endDate=(NOW + timedelta(minutes=1)).isoformat())

    def test_extended_period_is_new_revision(self):
        self.changed(endDate=(NOW + timedelta(minutes=8)).isoformat())

    def test_start_change_is_new_revision(self):
        self.changed(startDate=(NOW - timedelta(minutes=20)).isoformat())

    def test_code_change_is_new_revision(self):
        self.changed(voucher={'code': 'NEWCODE'})

    def test_destination_change_is_new_revision(self):
        self.changed(url='https://www.kabum.com.br/promocao/outro')

    def test_terms_change_is_new_revision(self):
        self.changed(terms='Compra mínima R$500 https://www.kabum.com.br/regras')

    def test_title_and_description_change(self):
        for field in ('title', 'description'):
            with self.subTest(field=field):
                self.changed(**{field: 'Outra condição'})
                self.gate._runtime_cache.clear()

    def test_absent_promotion_id(self):
        self.api.offers.return_value = [dict(self.item, promotionId=None)]
        self.reject('CUPOM_VALIDADE_NAO_CONFIRMADA')

    def test_removed_promotion(self):
        self.api.offers.return_value = []
        self.reject('CUPOM_VALIDADE_NAO_CONFIRMADA')

    def test_duplicate_identical_and_offset_equivalent(self):
        other = dict(self.item, endDate='2026-10-03T10:05:00-03:00', irrelevant='ignored')
        self.api.offers.return_value = [other, self.item]
        self.approve()

    def test_duplicate_conflicts_fail_closed(self):
        for field, value in (('endDate', (NOW + timedelta(minutes=1)).isoformat()),
                             ('voucher', {'code': 'OTHER'}), ('url', 'https://www.kabum.com.br/promocao/other')):
            with self.subTest(field=field):
                self.api.offers.return_value = [self.item, dict(self.item, **{field: value})]
                self.gate._runtime_cache.clear()
                self.reject('CUPOM_VALIDADE_NAO_CONFIRMADA')

    def test_not_started(self):
        self.moment = NOW - timedelta(hours=2)
        self.reject('CUPOM_NAO_INICIADO')

    def test_exact_start_inclusive(self):
        self.moment = NOW - timedelta(hours=1)
        approved = self.approve()
        self.assertTrue(self.authorize(approved, self.reserve(approved)))

    def test_exact_end_exclusive(self):
        self.moment = NOW + timedelta(minutes=5)
        self.reject('CUPOM_EXPIRADO')

    def test_expired_at_gate(self):
        self.moment = NOW + timedelta(minutes=6)
        self.reject('CUPOM_EXPIRADO')

    def test_cached_list_rechecks_clock(self):
        self.approve()
        self.moment += timedelta(minutes=6)
        self.reject('CUPOM_EXPIRADO')
        self.api.offers.assert_called_once()

    def test_api_failures_never_reuse_persisted_period(self):
        import requests
        for error in (requests.Timeout(), requests.ConnectionError(),
                      requests.HTTPError('403'), requests.HTTPError('429'), requests.HTTPError('500'), ValueError('invalid JSON')):
            with self.subTest(error=repr(error)):
                self.api.offers.side_effect = error
                self.gate._runtime_cache.clear()
                self.reject('VALIDACAO_INDISPONIVEL')

    def test_partial_wrong_advertiser_naive_and_type(self):
        for changes in ({'endDate':None}, {'advertiser':{'id':88}}, {'advertiser':[]},
                        {'endDate':'2026-10-03T13:05:00'}, {'type':'promotion'}, {'voucher':None}):
            with self.subTest(changes=changes):
                self.api.offers.return_value = [dict(self.item, **changes)]
                self.gate._runtime_cache.clear()
                self.reject('CUPOM_VALIDADE_NAO_CONFIRMADA')

    def test_legacy_without_native_unit_waits_for_collector(self):
        del self.original['kabum_coupon_unit']
        self.reject('CUPOM_VALIDADE_NAO_CONFIRMADA')

    def test_new_commercial_revision_persisted_normally(self):
        item = dict(self.item, endDate=(NOW + timedelta(minutes=1)).isoformat())
        old = self.selected
        self.original, self.selected = self.enqueue(item)
        self.api.offers.return_value = [item]
        self.assertNotEqual(old['digest'], self.selected['digest'])
        self.assertEqual(self.ledger.db.execute('SELECT expires FROM radar_queue').fetchone()[0], NOW.timestamp() + 60)
        self.approve()


class NativeAuthorizationTests(NativeFixture):
    def test_expired_after_gate_and_exact_end(self):
        approved = self.approve()
        day = self.reserve(approved)
        self.moment += timedelta(minutes=5)
        self.assertFalse(self.authorize(approved, day))
        self.assertEqual(self.ledger.authorization_reason, 'CUPOM_EXPIRADO')
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM posts').fetchone()[0], 0)

    def test_missing_proof(self):
        approved = self.approve()
        self.assertFalse(self.authorize(approved, self.reserve(approved), proof_override=True))
        self.assertEqual(self.ledger.authorization_reason, 'CUPOM_VALIDADE_NAO_CONFIRMADA')

    def test_divergent_proof(self):
        approved = self.approve()
        proof = copy.deepcopy(approved['_kabum_coupon_proof'])
        proof['unit']['end_date'] = (NOW + timedelta(hours=1)).isoformat()
        self.assertFalse(self.authorize(approved, self.reserve(approved), proof_override=True, proof=proof))

    def test_final_content_mutation_rejected(self):
        for field in ('product_id','promotion_id','code','title','description','terms','destination_url',
                      'affiliate_url','start_date','end_date','store','source','kind'):
            with self.subTest(field=field):
                approved = self.approve()
                day = self.reserve(approved)
                altered = dict(approved, **{field:'DIFFERENT'})
                # Use the original product/day so even changing final identity cannot bypass the guard.
                public = dict(altered)
                proof = public.pop('_kabum_coupon_proof')
                self.assertFalse(self.ledger.mark_sending(approved['product_id'], day, selected=self.selected,
                                 approved_offer=public, kabum_coupon_proof=proof))

    def test_proof_does_not_include_observation_clock(self):
        proof = self.approve()['_kabum_coupon_proof']
        self.assertNotIn('source_date', proof['content'])
        self.assertNotIn('queue_expires_at', proof['content'])
        self.assertNotIn('radar_score', proof['content'])

    def test_clock_read_after_complete_content_check(self):
        approved = self.approve()
        public = dict(approved)
        proof = public.pop('_kabum_coupon_proof')
        public['code'] = 'WRONG'
        clock = Mock(return_value=NOW.timestamp())
        self.assertEqual(coupons.authorization_status(proof,self.original,public,self.selected,
                         self.original['product_id'],clock), 'CUPOM_VALIDADE_NAO_CONFIRMADA')
        clock.assert_not_called()

    def test_selection_required(self):
        approved = self.approve()
        self.reserve(approved)
        self.assertFalse(self.ledger.mark_sending(approved['product_id'], '2026-10-03', approved_offer=approved,
                         kabum_coupon_proof=approved['_kabum_coupon_proof']))

    def test_other_day_preserved(self):
        approved = self.approve()
        day = self.reserve(approved)
        with self.ledger.db:
            self.ledger.db.execute("INSERT INTO posts(product,day,status) VALUES (?,?,'reserved')", (approved['product_id'],'2026-10-02'))
        self.moment += timedelta(minutes=6)
        self.assertFalse(self.authorize(approved,day))
        self.assertEqual(self.ledger.db.execute('SELECT day,status FROM posts').fetchall(), [('2026-10-02','reserved')])

    def preserve_state(self, status):
        approved = self.approve()
        day = self.reserve(approved)
        with self.ledger.db:
            self.ledger.db.execute('UPDATE posts SET status=?,message_id=77', (status,))
        before = self.ledger.db.execute('SELECT * FROM posts').fetchall()
        self.moment += timedelta(minutes=6)
        self.assertFalse(self.authorize(approved,day))
        self.assertEqual(self.ledger.db.execute('SELECT * FROM posts').fetchall(), before)

    def test_sent_preserved(self): self.preserve_state('sent')
    def test_sending_preserved(self): self.preserve_state('sending')
    def test_uncertain_preserved(self): self.preserve_state('uncertain')

    def test_replacement_binding_preserved(self):
        approved = self.approve()
        day = self.reserve(approved)
        newer = dict(self.selected, digest='new-binding')
        with self.ledger.db:
            self.ledger.db.execute('UPDATE publication_selections SET selection=?', (selection_id(newer),))
        self.moment += timedelta(minutes=6)
        self.assertFalse(self.authorize(approved,day))
        self.assertEqual(self.ledger.db.execute('SELECT status FROM posts').fetchone()[0],'reserved')

    def hold_lock(self, replace=False):
        approved = self.approve()
        day = self.reserve(approved)
        writer = sqlite3.connect(self.root/'publicacoes.sqlite3')
        writer.execute('BEGIN IMMEDIATE')
        started = threading.Event()
        results = []
        def worker():
            ledger = Ledger.__new__(Ledger)
            ledger.db = sqlite3.connect(self.root/'publicacoes.sqlite3', timeout=10)
            ledger.db.set_trace_callback(lambda sql: started.set() if sql == 'BEGIN IMMEDIATE' else None)
            try: results.append((self.authorize(approved,day,ledger=ledger), ledger.authorization_reason))
            except BaseException as error: results.append(error)
            finally: ledger.db.close()
        thread = threading.Thread(target=worker)
        thread.start()
        self.assertTrue(started.wait(5))
        if replace:
            item = dict(self.item, endDate=(NOW+timedelta(minutes=8)).isoformat())
            new = coupons.alert_from_offer(item,self.affiliate,NOW)
            writer.execute('UPDATE radar_queue SET payload=?', (json.dumps(new),))
        else:
            self.moment += timedelta(minutes=6)
        writer.commit(); writer.close()
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results,[(False, 'RESERVA_INVALIDA' if replace else 'CUPOM_EXPIRADO')])
        self.assertEqual(self.ledger.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        if replace:
            self.assertEqual(json.loads(self.ledger.db.execute('SELECT payload FROM radar_queue').fetchone()[0]),new)

    def test_lock_crosses_end(self): self.hold_lock()
    def test_new_revision_during_lock_wait(self): self.hold_lock(replace=True)

    def crash(self, after):
        approved = self.approve()
        day = self.reserve(approved)
        payload = self.root/'proof.json'
        payload.write_text(json.dumps({'approved':approved,'selected':self.selected,'day':day}))
        script = """
import json,os,sys,time
from pathlib import Path
from ofertas_core import Ledger
body=json.loads(Path(sys.argv[1]).read_text())
time.time=lambda: 1791032400.0
ledger=Ledger(Path(sys.argv[1]).parent/'publicacoes.sqlite3')
approved=body['approved']; proof=approved.pop('_kabum_coupon_proof')
if sys.argv[2]=='after':
 assert ledger.mark_sending(approved['product_id'],body['day'],selected=body['selected'],approved_offer=approved,kabum_coupon_proof=proof)
os._exit(19)
"""
        result = subprocess.run([sys.executable,'-c',script,str(payload),'after' if after else 'before'],
                                cwd=Path(__file__).resolve().parents[1],env={'PATH':'/usr/bin:/bin'},capture_output=True,text=True)
        self.assertEqual(result.returncode,19,result.stderr)
        restarted = Ledger(self.root/'publicacoes.sqlite3')
        try:
            restarted.reconcile_reservations()
            states = restarted.db.execute('SELECT status FROM posts').fetchall()
            self.assertEqual(states,[('uncertain',)] if after else [])
            self.assertEqual(restarted.db.execute('SELECT selection FROM publication_selections').fetchone()[0],selection_id(self.selected))
        finally: restarted.db.close()

    def test_crash_before_authorization_restart(self): self.crash(False)
    def test_crash_after_sending_restart_uncertain(self): self.crash(True)

    def test_confirmation_after_end_idempotent_preserves_new_revision(self):
        approved = self.approve()
        day = self.reserve(approved)
        self.assertTrue(self.authorize(approved,day))
        public = dict(approved); public.pop('_kabum_coupon_proof')
        new = coupons.alert_from_offer(dict(self.item,endDate=(NOW+timedelta(minutes=8)).isoformat()),self.affiliate,NOW)
        self.intelligence.enqueue([new],now=NOW.timestamp())
        self.moment += timedelta(minutes=6)
        self.ledger.finish(approved['product_id'],day,77,offer=public,channel='@audit',selected=self.selected)
        self.ledger.finish(approved['product_id'],day,77,offer=public,channel='@audit',selected=self.selected)
        self.assertEqual(json.loads(self.ledger.db.execute('SELECT payload FROM radar_queue').fetchone()[0]),new)
        self.assertEqual(self.ledger.db.execute('SELECT status,message_id FROM posts').fetchone(),('sent',77))
        self.assertEqual(self.ledger.db.execute('SELECT state,external_id FROM deliveries').fetchone(),('SENT','77'))


class NativePublisherTests(NativeFixture):
    def run_real_publisher(self, shortened=False, wait=False, send_hook=None):
        if shortened:
            self.api.offers.return_value = [dict(self.item,endDate=(NOW+timedelta(minutes=1)).isoformat())]
        with ExitStack() as stack:
            stack.enter_context(patch.object(publisher,'BASE',self.root))
            stack.enter_context(patch.object(publisher,'Ledger',return_value=self.ledger))
            stack.enter_context(patch.object(publisher.KabumAffiliate,'from_env',return_value=self.affiliate))
            for factory in (publisher.ShopeeAffiliate,publisher.MercadoLivreAffiliate,publisher.AmazonCreators):
                stack.enter_context(patch.object(factory,'from_env',side_effect=publisher.AffiliateError('disabled locally')))
            stack.enter_context(patch('mercadolivre_auto.AutoReader',return_value=Mock()))
            method = self.ledger.reserve
            def reserve(*args,**kwargs):
                result = method(*args,**kwargs)
                if wait: self.moment += timedelta(minutes=6)
                return result
            reservation = stack.enter_context(patch.object(self.ledger,'reserve',side_effect=reserve))
            authorization = stack.enter_context(patch.object(self.ledger,'mark_sending',wraps=self.ledger.mark_sending))
            def send(*args,**kwargs):
                self.assertNotIn('_kabum_coupon_proof',args[2])
                if send_hook: send_hook(args[2])
                return 77,None
            sender = stack.enter_context(patch.object(publisher.kabum_coupons,'send_alert',side_effect=send))
            shadow = stack.enter_context(patch.object(publisher.ShadowDistribution,'mirror_success',return_value={}))
            stack.enter_context(patch.object(publisher.time,'sleep',side_effect=KeyboardInterrupt))
            stack.enter_context(patch.dict('os.environ',{'TELEGRAM_TOKEN':'fake','TELEGRAM_CANAL':'@audit'}))
            stack.enter_context(patch('builtins.print'))
            with self.assertRaises(KeyboardInterrupt): publisher.run_publisher(SimpleNamespace(simular=False),Mock())
        self.network.assert_not_called()
        return reservation,authorization,sender,shadow

    def test_shortened_gate_blocks_before_reserve(self):
        reserve,authorize,sender,shadow = self.run_real_publisher(shortened=True)
        for call in (reserve,authorize,sender,shadow): call.assert_not_called()
        self.assertEqual(self.ledger.db.execute('SELECT reason FROM prepublication_gate').fetchone()[0],'REVISAO_COMERCIAL_ALTERADA')

    def test_new_revision_expired_after_gate_no_send(self):
        item = dict(self.item,endDate=(NOW+timedelta(minutes=1)).isoformat())
        self.original,self.selected = self.enqueue(item)
        self.api.offers.return_value = [item]
        reserve,authorize,sender,shadow = self.run_real_publisher(wait=True)
        reserve.assert_called_once(); authorize.assert_called_once()
        sender.assert_not_called(); shadow.assert_not_called()
        self.assertEqual(self.ledger.authorization_reason,'CUPOM_EXPIRADO')
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM posts').fetchone()[0],0)
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)

    def test_expiry_during_sender_keeps_confirmation(self):
        _,_,sender,shadow = self.run_real_publisher(send_hook=lambda offer:setattr(self,'moment',NOW+timedelta(minutes=6)))
        sender.assert_called_once(); shadow.assert_called_once()
        self.assertEqual(self.ledger.db.execute('SELECT status,message_id FROM posts').fetchone(),('sent',77))

    def test_uncertain_sender_preserves_protected_state(self):
        from telegram_api import TelegramSendError
        def uncertain(offer): raise TelegramSendError('uncertain', 'network timeout after dispatch')
        _,_,sender,shadow = self.run_real_publisher(send_hook=uncertain)
        sender.assert_called_once(); shadow.assert_not_called()
        self.assertEqual(self.ledger.db.execute('SELECT status FROM posts').fetchone()[0],'uncertain')

class NativeTransactionTests(NativeFixture):
    def test_two_connections_authorize_only_once(self):
        approved = self.approve()
        day = self.reserve(approved)
        start = threading.Barrier(3)
        results = []
        def worker():
            ledger = Ledger(self.root/'publicacoes.sqlite3')
            try:
                start.wait(5)
                results.append(self.authorize(approved,day,ledger=ledger))
            finally: ledger.db.close()
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads: thread.start()
        start.wait(5)
        for thread in threads: thread.join(10)
        self.assertEqual(sorted(results),[False,True])
        self.assertEqual(self.ledger.db.execute('SELECT status FROM posts').fetchall(),[('sending',)])

    def test_sql_failure_rolls_back_authorization(self):
        approved = self.approve()
        day = self.reserve(approved)
        with self.ledger.db:
            self.ledger.db.execute("CREATE TRIGGER reject_sending BEFORE UPDATE ON posts BEGIN SELECT RAISE(ABORT, 'injected'); END")
        before = self.ledger.db.execute('SELECT * FROM posts').fetchall()
        with self.assertRaises(sqlite3.IntegrityError): self.authorize(approved,day)
        self.assertEqual(self.ledger.db.execute('SELECT * FROM posts').fetchall(),before)
        self.assertFalse(self.ledger.db.in_transaction)

    def test_queue_ttl_remains_independent_defense(self):
        approved = self.approve()
        day = self.reserve(approved)
        with self.ledger.db:
            self.ledger.db.execute('UPDATE radar_queue SET expires=?',(NOW.timestamp()-1,))
        self.assertFalse(self.authorize(approved,day))
        self.assertEqual(self.ledger.authorization_reason,'RESERVA_INVALIDA')

    def test_local_period_not_only_queue_ttl(self):
        approved = self.approve()
        day = self.reserve(approved)
        with self.ledger.db:
            self.ledger.db.execute('UPDATE radar_queue SET expires=?',(NOW.timestamp()+3600,))
        self.moment += timedelta(minutes=6)
        self.assertFalse(self.authorize(approved,day))
        self.assertEqual(self.ledger.authorization_reason,'CUPOM_EXPIRADO')
