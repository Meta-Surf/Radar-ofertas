"""Prova da lista final do Gate, clocks aware e SQLite temporário, sem rede."""
import copy
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bot_ofertas_revisao as publisher
import cupons_mercadolivre as ml
import cupons_shopee as shopee
from cupom_validade import fingerprint
from fila_ofertas_sqlite import CapturedOfferQueue
from ofertas_core import Ledger
from prepublicacao import GateReject, PrePublicationGate
from revisao_publicacao import selection, selection_id

NOW = datetime(2026, 10, 3, 10, tzinfo=shopee.COMMERCIAL_TZ)
STORES = ('Mercado Livre', 'Shopee')


def coupon(store, conditions=None, message_id=1):
    conditions = conditions or ['Válido até 03/10/2026 às 10:01']
    if store == 'Mercado Livre':
        body = conditions[0] if len(conditions) == 1 else '\n'.join(conditions)
        if not ml.entries(body):
            body = '10% OFF: TESTEA\n' + body
        result = ml.build_alert([SimpleNamespace(raw_text='CUPONS MERCADO LIVRE\n' + body,
                                date=NOW, id=message_id)], -1)
        return dict(result, source_revision_at=NOW.isoformat())
    entries = [{'url': f'https://shopee.com.br/m/coupon-{i}', 'conditions': text}
               for i, text in enumerate(conditions)]
    return dict(kind='coupon_alert', source='telegram', store=store, entries=entries,
                product_id=shopee.alert_key(entries, NOW.isoformat()), source_date=NOW.isoformat(),
                chat_id=-1, message_id=message_id, source_revision_at=NOW.isoformat())


class ProofFixture(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.root = Path(self.temp)
        self.path = self.root / 'publicacoes.sqlite3'
        self.moment = NOW
        self.stack.enter_context(patch('time.time', side_effect=lambda: self.moment.timestamp()))
        for target in ('prepublicacao.datetime', 'ofertas_core.datetime', 'bot_ofertas_revisao.datetime'):
            clock = self.stack.enter_context(patch(target, wraps=datetime))
            clock.now.side_effect = lambda tz=None: self.moment.astimezone(tz) if tz else self.moment.replace(tzinfo=None)
        self.ledger = Ledger(self.path)
        self.stack.callback(self.ledger.db.close)
        self.queue = CapturedOfferQueue(self.path, now=lambda: self.moment.timestamp())
        self.stack.callback(self.queue.close)
        self.network = self.stack.enter_context(patch('requests.sessions.Session.request', side_effect=AssertionError('network forbidden')))
        self.affiliate = Mock()
        self.affiliate.generate_coupon_link.side_effect = lambda url: 'https://s.shopee.com.br/' + url.rsplit('/', 1)[-1]
        self.gate = PrePublicationGate(self.root, self.ledger.db, shopee=self.affiliate)

    def evaluate(self, store='Mercado Livre', conditions=None, message_id=1):
        original = coupon(store, conditions, message_id)
        self.queue.replace_capture([original], now=self.moment.timestamp())
        row = next(row for row in self.queue.pending(include_selection=True)
                   if row.get('message_id') == message_id)
        selected = row.pop('_queue_selection')
        prepared = ml.prepare_alert(original) if store == 'Mercado Livre' else shopee.prepare_alert(self.affiliate, original)
        approved = self.gate.validate(row, prepared, '@audit', selected=selected)
        return original, selected, approved

    def reserve(self, selected, approved):
        day = self.ledger.reserve(approved['product_id'], offer=approved, channel='@audit', selected=selected)
        self.assertIsNotNone(day)
        return day

    def authorize(self, selected, approved, day, ledger=None, proof_override=False, proof=None):
        public = dict(approved)
        extracted = public.pop('_coupon_deadline_proof', None)
        return (ledger or self.ledger).mark_sending(public['product_id'], day, selected=selected,
                 approved_offer=public, coupon_deadline_proof=proof if proof_override else extracted)

    def reset(self):
        tables = {row[0] for row in self.ledger.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        with self.ledger.db:
            for table in ('posts', 'publication_selections', 'captured_queue', 'prepublication_gate', 'deliveries', 'price_history'):
                if table in tables:
                    self.ledger.db.execute('DELETE FROM ' + table)
        self.moment = NOW

    def run_publisher(self, store, reserve_hook=None, sender_hook=None, gate_hook=None):
        self.queue.replace_capture([coupon(store)], now=self.moment.timestamp())
        with ExitStack() as stack:
            stack.enter_context(patch.object(publisher, 'BASE', self.root))
            stack.enter_context(patch.object(publisher, 'Ledger', return_value=self.ledger))
            stack.enter_context(patch.object(publisher, 'CapturedOfferQueue',
                                side_effect=lambda path: CapturedOfferQueue(path, now=lambda: self.moment.timestamp())))
            stack.enter_context(patch.object(publisher.ShopeeAffiliate, 'from_env', return_value=self.affiliate))
            for factory in (publisher.MercadoLivreAffiliate, publisher.KabumAffiliate, publisher.AmazonCreators):
                stack.enter_context(patch.object(factory, 'from_env', side_effect=publisher.AffiliateError('disabled locally')))
            stack.enter_context(patch('mercadolivre_auto.AutoReader', return_value=Mock()))
            method = self.ledger.reserve
            def reserve(*args, **kwargs):
                day = method(*args, **kwargs)
                if reserve_hook:
                    reserve_hook()
                return day
            reserve_spy = stack.enter_context(patch.object(self.ledger, 'reserve', side_effect=reserve))
            authorization = stack.enter_context(patch.object(self.ledger, 'mark_sending', wraps=self.ledger.mark_sending))
            def send(*args, **kwargs):
                if sender_hook:
                    sender_hook(args[2])
                return 77, None
            target = publisher.ml_coupons if store == 'Mercado Livre' else publisher
            sender = stack.enter_context(patch.object(target, 'send_alert', side_effect=send))
            shadow = stack.enter_context(patch.object(publisher.ShadowDistribution, 'mirror_success', return_value={}))
            if gate_hook:
                validate = PrePublicationGate.validate
                def altered(gate, *args, **kwargs):
                    result = validate(gate, *args, **kwargs)
                    gate_hook(result)
                    return result
                stack.enter_context(patch.object(PrePublicationGate, 'validate', altered))
            stack.enter_context(patch.dict('os.environ', {'TELEGRAM_TOKEN': 'fake', 'TELEGRAM_CANAL': '@audit'}))
            stack.enter_context(patch.object(publisher.time, 'sleep', side_effect=KeyboardInterrupt))
            stack.enter_context(patch('builtins.print'))
            with self.assertRaises(KeyboardInterrupt):
                publisher.run_publisher(SimpleNamespace(simular=False), Mock())
            self.network.assert_not_called()
            return reserve_spy, authorization, sender, shadow


class MLProofTests(ProofFixture):
    def test_global_before_and_after_list_is_active(self):
        for body in ('Válido até 03/10/2026 às 10:01\n10% OFF: TESTEA\n20% OFF: TESTEB',
                     '10% OFF: TESTEA\n20% OFF: TESTEB\nVálido até 03/10/2026 às 10:01'):
            _, selected, approved = self.evaluate(conditions=[body])
            self.assertEqual(len(approved['_coupon_deadline_proof']['deadlines']['global']), 1)
            self.assertTrue(self.authorize(selected, approved, self.reserve(selected, approved)))
            self.reset()

    def test_global_expired_after_gate_and_exact_limit(self):
        for moment in (NOW + timedelta(minutes=1), NOW + timedelta(minutes=2)):
            _, selected, approved = self.evaluate()
            day = self.reserve(selected, approved)
            self.moment = moment
            self.assertFalse(self.authorize(selected, approved, day))
            self.assertEqual(self.ledger.authorization_reason, 'CUPOM_EXPIRADO')
            self.reset()

    def test_inline_individual_active_then_expired(self):
        _, selected, approved = self.evaluate(conditions=['10% OFF até 03/10/2026 10:01: TESTEA'])
        self.assertEqual(approved['_coupon_deadline_proof']['deadlines']['global'], [{'kind': 'unspecified', 'ends': []}])
        day = self.reserve(selected, approved)
        self.moment += timedelta(minutes=2)
        self.assertFalse(self.authorize(selected, approved, day))

    def test_inline_individual_active_authorizes(self):
        _, selected, approved = self.evaluate(conditions=['10% OFF até 03/10/2026 10:01: TESTEA'])
        self.moment += timedelta(seconds=59)
        self.assertTrue(self.authorize(selected, approved, self.reserve(selected, approved)))

    def test_filtered_list_proof_contains_only_approved_entry(self):
        original, selected, approved = self.evaluate(conditions=[
            '10% OFF até 03/10/2026 10:05: TESTEA\n20% OFF até 02/10/2026: TESTEB'])
        proof = approved['_coupon_deadline_proof']
        self.assertEqual([entry['code'] for entry in proof['content']['entries']], ['TESTEA'])
        self.assertNotIn('TESTEB', proof['content']['text'])
        self.assertNotEqual(approved['product_id'], original['product_id'])
        self.assertEqual(len(proof['deadlines']['entries']), 1)
        self.assertTrue(self.authorize(selected, approved, self.reserve(selected, approved)))

    def test_one_of_two_approved_entries_expires_blocks_whole_list(self):
        _, selected, approved = self.evaluate(conditions=[
            '10% OFF até 03/10/2026 10:05: TESTEA\n20% OFF até 03/10/2026 10:01: TESTEB'])
        before = copy.deepcopy(approved)
        day = self.reserve(selected, approved)
        self.moment += timedelta(minutes=2)
        self.assertFalse(self.authorize(selected, approved, day))
        self.assertEqual(approved, before)
        self.assertEqual(len(approved['entries']), 2)

    def test_ambiguous_global_never_builds_approved_proof(self):
        with self.assertRaises(GateReject) as caught:
            self.evaluate(conditions=['10% OFF: TESTEA\nVálido até 03/10/2026 10:01\n20% OFF: TESTEB'])
        self.assertEqual(caught.exception.reason, 'CUPOM_VALIDADE_NAO_CONFIRMADA')

    def test_without_deadline_is_explicitly_unspecified(self):
        _, selected, approved = self.evaluate(conditions=['10% OFF mínimo R$100: TESTEA'])
        proof = approved['_coupon_deadline_proof']
        self.assertEqual(proof['deadlines']['entries'][0]['deadline'], {'kind': 'unspecified', 'ends': []})
        self.moment += timedelta(minutes=2)
        self.assertTrue(self.authorize(selected, approved, self.reserve(selected, approved)))

    def test_day_without_time_ends_at_next_commercial_midnight(self):
        _, selected, approved = self.evaluate(conditions=['Válido até 03/10/2026'])
        self.assertEqual(approved['_coupon_deadline_proof']['deadlines']['global'][0]['ends'],
                         [datetime(2026, 10, 4, tzinfo=shopee.COMMERCIAL_TZ).timestamp()])

    def test_global_wrapped_label_preserves_association(self):
        _, _, approved = self.evaluate(conditions=['10% OFF: TESTEA\nVálido até\n03/10/2026 às 10:01'])
        self.assertEqual(approved['_coupon_deadline_proof']['deadlines']['global'][0]['kind'], 'explicit')


class ShopeeProofTests(ProofFixture):
    def test_active_single_entry_and_deadline_exclusive(self):
        _, selected, approved = self.evaluate('Shopee')
        day = self.reserve(selected, approved)
        self.moment += timedelta(minutes=1)
        self.assertFalse(self.authorize(selected, approved, day))
        self.assertEqual(self.ledger.authorization_reason, 'CUPOM_EXPIRADO')

    def test_two_active_entries_are_authorized_unchanged(self):
        _, selected, approved = self.evaluate('Shopee', ['Válido até 03/10/2026 10:01', 'Válido até 03/10/2026 10:05'])
        before = copy.deepcopy(approved)
        self.assertTrue(self.authorize(selected, approved, self.reserve(selected, approved)))
        self.assertEqual(approved, before)

    def test_one_approved_entry_expires_blocks_entire_list(self):
        _, selected, approved = self.evaluate('Shopee', ['Válido até 03/10/2026 10:01', 'Válido até 03/10/2026 10:05'])
        day = self.reserve(selected, approved)
        self.moment += timedelta(minutes=2)
        self.assertFalse(self.authorize(selected, approved, day))
        self.assertEqual(len(approved['entries']), 2)

    def test_expired_removed_before_proof_and_identity_is_final(self):
        original, selected, approved = self.evaluate('Shopee', ['Válido até 02/10/2026', 'Válido até 03/10/2026 10:05'])
        proof = approved['_coupon_deadline_proof']
        self.assertEqual(len(proof['content']['entries']), 1)
        self.assertNotEqual(approved['product_id'], original['product_id'])
        self.assertEqual(proof['content']['entries'][0]['url'], original['entries'][1]['url'])
        self.assertTrue(self.authorize(selected, approved, self.reserve(selected, approved)))

    def test_unspecified_entry_not_given_operational_ttl(self):
        _, selected, approved = self.evaluate('Shopee', ['20% OFF mínimo R$100'])
        self.assertEqual(approved['_coupon_deadline_proof']['deadlines']['entries'][0]['deadline'], {'kind': 'unspecified', 'ends': []})
        self.moment += timedelta(minutes=2)
        self.assertTrue(self.authorize(selected, approved, self.reserve(selected, approved)))

    def test_unknown_entry_is_filtered_without_weakening_remaining_proof(self):
        _, selected, approved = self.evaluate('Shopee', ['expira amanhã', 'Válido até 03/10/2026 10:05'])
        self.assertEqual(len(approved['_coupon_deadline_proof']['deadlines']['entries']), 1)
        self.assertTrue(self.authorize(selected, approved, self.reserve(selected, approved)))


class CouponAuthorizationTests(ProofFixture):
    def test_expiration_during_proof_verification_reads_clock_last(self):
        _, selected, approved = self.evaluate()
        day = self.reserve(selected, approved)
        def finish_at_deadline(value):
            result = fingerprint(value)
            self.moment = NOW + timedelta(minutes=1)
            return result
        with patch('cupom_validade.fingerprint', side_effect=finish_at_deadline):
            self.assertFalse(self.authorize(selected, approved, day))
        self.assertEqual(self.ledger.authorization_reason, 'CUPOM_EXPIRADO')

    def test_proof_from_other_selection_refuses(self):
        _, selected, approved = self.evaluate()
        proof = copy.deepcopy(approved['_coupon_deadline_proof'])
        proof['selection']['key'] += ':other'
        proof['digest'] = fingerprint({k:v for k,v in proof.items() if k != 'digest'})
        self.assertFalse(self.authorize(selected, approved, self.reserve(selected, approved), proof_override=True, proof=proof))

    def test_no_selection_cannot_use_standalone_gate_proof(self):
        _, _, approved = self.evaluate()
        day = self.ledger.reserve(approved['product_id'], offer=approved, channel='@audit')
        self.assertIsNotNone(day)
        self.assertFalse(self.authorize(None, approved, day))
        self.assertEqual(self.ledger.db.execute('SELECT status FROM posts').fetchone()[0], 'reserved')

    def test_expiration_does_not_remove_other_origin_or_queue_revision(self):
        for store in STORES:
            _, selected, approved = self.evaluate(store)
            day = self.reserve(selected, approved)
            other = coupon(store, message_id=2)
            self.queue.replace_capture([other], now=self.moment.timestamp())
            before = self.queue.db.execute('SELECT queue_key,payload FROM captured_queue ORDER BY queue_key').fetchall()
            self.moment += timedelta(minutes=2)
            self.assertFalse(self.authorize(selected, approved, day))
            self.assertEqual(before, self.queue.db.execute('SELECT queue_key,payload FROM captured_queue ORDER BY queue_key').fetchall())
            self.reset()

    def test_fresh_gate_after_whole_list_refusal_can_approve_remaining_content(self):
        for store in STORES:
            conditions = (['10% OFF até 03/10/2026 10:05: TESTEA\n20% OFF até 03/10/2026 10:01: TESTEB']
                          if store == 'Mercado Livre' else ['Válido até 03/10/2026 10:05', 'Válido até 03/10/2026 10:01'])
            original, selected, approved = self.evaluate(store, conditions)
            day = self.reserve(selected, approved)
            self.moment += timedelta(minutes=2)
            self.assertFalse(self.authorize(selected, approved, day))
            prepared = ml.prepare_alert(original) if store == 'Mercado Livre' else shopee.prepare_alert(self.affiliate, original)
            remaining = self.gate.validate(original, prepared, '@audit', selected=selected)
            self.assertEqual(len(remaining['entries']), 1)
            self.assertNotEqual(remaining['product_id'], approved['product_id'])
            self.assertTrue(self.authorize(selected, remaining, self.reserve(selected, remaining)))
            self.reset()

    def test_proof_deterministic_without_observational_times(self):
        for store in STORES:
            original, selected, approved = self.evaluate(store)
            self.moment += timedelta(seconds=1)
            prepared = ml.prepare_alert(original) if store == 'Mercado Livre' else shopee.prepare_alert(self.affiliate, original)
            repeated = self.gate.validate(original, prepared, '@audit', selected=selected)
            self.assertEqual(approved['_coupon_deadline_proof'], repeated['_coupon_deadline_proof'])
            self.reset()

    def test_missing_malformed_unknown_and_digest_conflicting_proof_fail_closed(self):
        for store in STORES:
            for mode in ('missing', 'empty', 'digest', 'unknown', 'association', 'boolean_end'):
                _, selected, approved = self.evaluate(store)
                proof = copy.deepcopy(approved['_coupon_deadline_proof'])
                if mode == 'missing': proof = None
                elif mode == 'empty': proof = {}
                elif mode == 'digest': proof['content_digest'] = '0' * 64
                elif mode == 'unknown': proof['deadlines']['entries'][0]['deadline']['kind'] = 'unknown'
                elif mode == 'association': proof['deadlines']['entries'][0]['entry_digest'] = '0' * 64
                else: proof['deadlines']['entries'][0]['deadline']['ends'] = [True]
                if isinstance(proof, dict) and mode not in ('empty', 'digest'):
                    proof['digest'] = fingerprint({k:v for k,v in proof.items() if k != 'digest'})
                day = self.reserve(selected, approved)
                self.assertFalse(self.authorize(selected, approved, day, proof_override=True, proof=proof), (store, mode))
                self.assertEqual(self.ledger.authorization_reason, 'CUPOM_VALIDADE_NAO_CONFIRMADA')
                self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM posts').fetchone()[0], 0)
                self.reset()

    def test_every_commercial_field_mutated_after_gate_refuses(self):
        for store in STORES:
            for field in ('text', 'entries', 'product_id', 'store', 'source_date', 'kind', 'source', 'affiliate_generated'):
                _, selected, approved = self.evaluate(store)
                day = self.reserve(selected, approved)
                changed = copy.deepcopy(approved)
                if field == 'entries': changed['entries'][0]['conditions'] = '20% OFF'
                elif field == 'affiliate_generated': changed[field] = not bool(changed.get(field))
                else: changed[field] = str(changed.get(field)) + ':changed'
                # product/day de reserva continuam os originais: não selecionar outra linha.
                public = dict(changed); proof = public.pop('_coupon_deadline_proof')
                self.assertFalse(self.ledger.mark_sending(approved['product_id'], day, selected=selected,
                                 approved_offer=public, coupon_deadline_proof=proof), (store, field))
                self.reset()

    def test_original_payload_or_selection_divergence_refuses(self):
        _, selected, approved = self.evaluate()
        day = self.reserve(selected, approved)
        proof = copy.deepcopy(approved['_coupon_deadline_proof'])
        proof['original_digest'] = '0' * 64
        proof['digest'] = fingerprint({k:v for k,v in proof.items() if k != 'digest'})
        self.assertFalse(self.authorize(selected, approved, day, proof_override=True, proof=proof))

    def test_independent_connection_new_revision_preserved(self):
        for store in STORES:
            _, selected, approved = self.evaluate(store)
            day = self.reserve(selected, approved)
            raw = json.dumps(coupon(store, ['Válido até 03/10/2026 às 10:05']), ensure_ascii=False)
            with sqlite3.connect(self.path) as db:
                db.execute('UPDATE captured_queue SET payload=? WHERE queue_key=?', (raw, selected['key']))
            self.assertFalse(self.authorize(selected, approved, day))
            self.assertEqual(self.queue.db.execute('SELECT payload FROM captured_queue').fetchone()[0], raw)
            self.reset()

    def waiting_case(self, store, replace=False):
        _, selected, approved = self.evaluate(store)
        day = self.reserve(selected, approved)
        ready, go, waiting = threading.Event(), threading.Event(), threading.Event()
        results, errors = [], []
        def worker():
            ledger = None
            try:
                ledger = Ledger(self.path)
                ledger.db.set_trace_callback(lambda sql: waiting.set() if sql == 'BEGIN IMMEDIATE' else None)
                ready.set(); go.wait(3)
                results.append(self.authorize(selected, approved, day, ledger))
            except BaseException as error: errors.append(error); ready.set(); waiting.set()
            finally:
                if ledger: ledger.db.close()
        thread = threading.Thread(target=worker); thread.start()
        self.assertTrue(ready.wait(3))
        blocker = sqlite3.connect(self.path)
        replacement = None
        try:
            blocker.execute('BEGIN IMMEDIATE'); go.set(); self.assertTrue(waiting.wait(3))
            self.moment = NOW + timedelta(minutes=2)
            if replace:
                raw = json.dumps(coupon(store, ['Válido até 03/10/2026 10:05']), ensure_ascii=False)
                replacement = selection('captured_queue', selected['key'], raw)
                blocker.execute('UPDATE captured_queue SET payload=? WHERE queue_key=?', (raw, selected['key']))
                blocker.execute('UPDATE publication_selections SET selection=? WHERE product=? AND day=?',
                                (selection_id(replacement), approved['product_id'], day))
                blocker.commit()
        finally: blocker.rollback(); blocker.close()
        thread.join(5); self.assertFalse(thread.is_alive()); self.assertEqual(errors, []); self.assertEqual(results, [False])
        if replace:
            self.assertEqual(self.ledger.db.execute('SELECT status FROM posts').fetchone()[0], 'reserved')
            self.assertEqual(self.ledger.db.execute('SELECT selection FROM publication_selections').fetchone()[0], selection_id(replacement))
        else:
            self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM posts').fetchone()[0], 0)

    def test_lock_wait_after_deadline_for_both_stores(self):
        for store in STORES:
            self.waiting_case(store); self.reset()

    def test_replacement_binding_during_wait_not_cancelled(self):
        for store in STORES:
            self.waiting_case(store, replace=True); self.reset()

    def test_another_day_and_protected_states_never_regress(self):
        for store in STORES:
            _, selected, approved = self.evaluate(store)
            day = self.reserve(selected, approved)
            with self.ledger.db:
                self.ledger.db.execute("INSERT INTO posts(product,day,status,message_id) VALUES(?,?,'sent',88)",
                                       (approved['product_id'], '2000-01-01'))
            self.moment += timedelta(minutes=2)
            for state in ('sent', 'sending', 'uncertain'):
                with self.ledger.db:
                    self.ledger.db.execute('UPDATE posts SET status=?,message_id=77 WHERE day=?', (state, day))
                self.assertFalse(self.authorize(selected, approved, day))
                self.assertEqual(self.ledger.db.execute('SELECT status,message_id FROM posts WHERE day=?', (day,)).fetchone(), (state,77))
                self.assertEqual(self.ledger.db.execute("SELECT status,message_id FROM posts WHERE day='2000-01-01'").fetchone(), ('sent',88))
            self.reset()

    def test_eight_connections_only_one_authorization(self):
        _, selected, approved = self.evaluate()
        day = self.reserve(selected, approved)
        barrier = threading.Barrier(8)
        def worker(_):
            ledger = Ledger(self.path)
            try: barrier.wait(); return self.authorize(selected, approved, day, ledger)
            finally: ledger.db.close()
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(worker, range(8))), 1)

    def test_crash_after_sending_restart_uncertain_preserves_new_revision(self):
        for store in STORES:
            original, selected, approved = self.evaluate(store)
            public = dict(approved); proof = public.pop('_coupon_deadline_proof')
            code = '''import json,sys,time,os
from ofertas_core import Ledger
d=json.loads(sys.argv[1]);time.time=lambda:d['now']
l=Ledger(d['path']);o=d['offer'];s=d['selected']
day=l.reserve(o['product_id'],offer=o,channel='@audit',selected=s)
assert l.mark_sending(o['product_id'],day,selected=s,coupon_deadline_proof=d['proof'],approved_offer=o)
os._exit(19)
'''
            root = Path(__file__).resolve().parents[1]
            data = dict(now=self.moment.timestamp(), path=str(self.path), offer=public, selected=selected, proof=proof)
            process = subprocess.run([sys.executable, '-c', code, json.dumps(data)], cwd=root,
                      env={'PYTHONPATH':str(root),'PATH':os.defpath}, capture_output=True,text=True,timeout=10)
            self.assertEqual(process.returncode, 19, process.stderr)
            new = coupon(store, ['Válido até 03/10/2026 10:05'])
            new['source_revision_at'] = (NOW + timedelta(seconds=1)).isoformat()
            self.queue.replace_capture([new], now=self.moment.timestamp())
            self.assertEqual(self.ledger.reconcile_reservations()['moved_to_uncertain'], 1)
            self.assertEqual(self.ledger.db.execute('SELECT status FROM posts').fetchone()[0], 'uncertain')
            self.assertEqual(json.loads(self.queue.db.execute('SELECT payload FROM captured_queue').fetchone()[0]), new)
            self.reset()

    def test_restart_before_authorization_releases_abandoned_own_reservation(self):
        _, selected, approved = self.evaluate()
        day = self.reserve(selected, approved)
        reopened = Ledger(self.path)
        self.stack.callback(reopened.db.close)
        self.assertEqual(reopened.reconcile_reservations()['released_abandoned_reserved'], 1)
        self.assertFalse(self.authorize(selected, approved, day, reopened))
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM deliveries').fetchone()[0], 0)

    def test_unexpected_local_failure_rolls_back_integrally(self):
        _, selected, approved = self.evaluate()
        day = self.reserve(selected, approved)
        with patch('cupom_validade.authorization_status', side_effect=RuntimeError('local failure')):
            with self.assertRaises(RuntimeError): self.authorize(selected, approved, day)
        self.assertEqual(self.ledger.db.execute('SELECT status FROM posts').fetchone()[0], 'reserved')


class CouponPublisherTests(ProofFixture):
    def test_original_after_reserve_expiration_blocks_sender_and_shadow(self):
        for store in STORES:
            def expire(): self.moment = NOW + timedelta(minutes=2)
            reserve, authorization, sender, shadow = self.run_publisher(store, reserve_hook=expire)
            self.assertEqual((reserve.call_count, authorization.call_count), (1,1))
            sender.assert_not_called(); shadow.assert_not_called()
            self.assertEqual(self.ledger.authorization_reason, 'CUPOM_EXPIRADO')
            self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM posts').fetchone()[0], 0)
            self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM deliveries').fetchone()[0], 0)
            self.reset()

    def test_already_expired_blocks_before_reservation(self):
        for store in STORES:
            self.moment = NOW + timedelta(minutes=2)
            reserve, authorization, sender, shadow = self.run_publisher(store)
            for spy in (reserve, authorization, sender, shadow): spy.assert_not_called()
            self.reset()

    def test_active_sends_same_final_content_without_proof_in_sender_shadow_delivery(self):
        for store in STORES:
            _, _, sender, shadow = self.run_publisher(store)
            self.assertEqual(sender.call_count, 1)
            public = sender.call_args.args[2]
            self.assertNotIn('_coupon_deadline_proof', public)
            self.assertEqual(shadow.call_args.args[0], public)
            self.assertNotIn('_coupon_deadline_proof', json.loads(self.ledger.db.execute('SELECT payload FROM deliveries').fetchone()[0]))
            self.reset()

    def test_tampered_entries_after_gate_never_send(self):
        for store in STORES:
            def tamper(offer): offer['entries'][0]['conditions'] = '20% OFF alterado'
            _, _, sender, shadow = self.run_publisher(store, gate_hook=tamper)
            sender.assert_not_called(); shadow.assert_not_called()
            self.assertEqual(self.ledger.authorization_reason, 'CUPOM_VALIDADE_NAO_CONFIRMADA')
            self.reset()

    def test_expiration_during_sender_confirms_original_and_preserves_new_revision(self):
        for store in STORES:
            def during_send(public):
                self.moment = NOW + timedelta(minutes=2)
                new = coupon(store, ['Válido até 03/10/2026 10:05'])
                new['source_revision_at'] = self.moment.isoformat()
                self.queue.replace_capture([new], now=self.moment.timestamp())
            _, _, sender, shadow = self.run_publisher(store, sender_hook=during_send)
            self.assertEqual((sender.call_count, shadow.call_count), (1,1))
            self.assertEqual(self.ledger.db.execute('SELECT status,message_id FROM posts').fetchone(), ('sent',77))
            old = json.loads(self.ledger.db.execute('SELECT payload FROM deliveries').fetchone()[0])
            new = json.loads(self.queue.db.execute('SELECT payload FROM captured_queue').fetchone()[0])
            self.assertNotEqual(old['product_id'], new['product_id'])
            self.reset()

    def test_unexpected_sender_failure_keeps_uncertain_binding(self):
        def fail(public): raise RuntimeError('unknown external result')
        _, _, sender, shadow = self.run_publisher('Mercado Livre', sender_hook=fail)
        self.assertEqual(sender.call_count, 1); shadow.assert_not_called()
        self.assertEqual(self.ledger.db.execute('SELECT status FROM posts').fetchone()[0], 'uncertain')
        self.assertEqual(self.ledger.db.execute('SELECT state FROM deliveries').fetchone()[0], 'UNCERTAIN')

    def test_sqlite_authorization_failure_preserves_queue_and_never_sends(self):
        with patch.object(self.ledger, 'mark_sending', side_effect=sqlite3.OperationalError('busy')):
            _, _, sender, shadow = self.run_publisher('Mercado Livre')
        sender.assert_not_called(); shadow.assert_not_called()
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM posts').fetchone()[0], 0)
        self.assertEqual(self.queue.db.execute('SELECT count(*) FROM captured_queue').fetchone()[0], 1)
