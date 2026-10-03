"""Consumidor real, conexões independentes e crashes; nenhum envio externo."""
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
import mercadolivre_manual as manual
import radar_kabum as kabum
from fila_ofertas_sqlite import CapturedOfferQueue
from inteligencia_ofertas import Intelligence
from kabum_afiliados import KabumAffiliate
from metricas_fontes import SourceMetrics
from ofertas_core import Ledger
from prepublicacao import PrePublicationGate, GateReject
from publisher_backoff import PublisherBackoff, offer_revision
from revisao_publicacao import catalog_revision, retry_key, selection_id
from telegram_api import TelegramSendError
from tests.test_prepublicacao import shopee_offer, ml_offer
from tests.test_radar_publication_selection import feed

ROOT = Path(__file__).resolve().parents[1]


class RevisionFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.path = self.root / 'publicacoes.sqlite3'
        self.ledger = Ledger(self.path); self.addCleanup(self.ledger.db.close)
        self.i = Intelligence(self.ledger.db)
        self.q = CapturedOfferQueue(self.path); self.addCleanup(self.q.close)
        self.offer = dict(shopee_offer(source='telegram'), chat_id=-1, message_id=7,
                          name='Fixture', api_image='https://fixture.invalid/old.jpg',
                          capture_digest='old')
        self.offer['source_revision_at'] = self.offer['source_date']
        self.offer['source_revision_members'] = [[7, self.offer['source_date']]]
        self.q.replace_capture([self.offer])
        self.selected = self.q.pending(include_selection=True)[0]['_queue_selection']

    def edit(self, **fields):
        stamp = (datetime.fromisoformat(self.offer['source_revision_at']) + timedelta(seconds=1)).isoformat()
        new = dict(self.offer, capture_digest='new', source_revision_at=stamp,
                   source_revision_members=[[7, stamp]])
        if 'source_revision_members' in fields:
            fields['source_revision_members']=[[7,stamp],[8,stamp]]
        new.update(fields)
        other = CapturedOfferQueue(self.path)
        try: self.assertEqual(other.replace_capture([new]), 1)
        finally: other.close()
        return new

    def reserve(self):
        return self.ledger.reserve(self.offer['product_id'], offer=self.offer,
                                   channel='@audit', selected=self.selected)

    def authorize(self, day, **kwargs):
        return self.ledger.mark_sending(self.offer['product_id'], day,
                                        selected=self.selected, **kwargs)


class RevisionTests(RevisionFixture):
    def test_change_before_reservation_cannot_reserve_old_revision(self):
        self.edit(price='150,00')
        self.assertIsNone(self.reserve())
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM posts').fetchone()[0], 0)

    def test_price_coupon_text_image_and_album_change_between_reserve_and_send(self):
        for fields in [{'price':'150,00'}, {'coupon':'NOVO'}, {'name':'Texto novo'},
                       {'api_image':'https://fixture.invalid/new.jpg'},
                       {'source_revision_members':[[7, self.offer['source_date']], [8, self.offer['source_date']]]}]:
            with self.subTest(fields=fields):
                day = self.reserve(); self.assertIsNotNone(day)
                self.edit(**fields)
                self.assertFalse(self.authorize(day))
                self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM posts').fetchone()[0], 0)
                self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM deliveries').fetchone()[0], 0)
                self.assertEqual(len(self.q.pending()), 1)
                # A próxima iteração usa a revisão atual, nunca a autoridade antiga.
                self.offer = self.q.pending()[0]
                self.selected = self.q.pending(include_selection=True)[0]['_queue_selection']

    def test_update_after_authorization_preserves_sent_payload_and_new_queue(self):
        day = self.reserve(); self.assertTrue(self.authorize(day))
        self.edit(price='150,00')
        self.ledger.finish(self.offer['product_id'], day, 77, self.offer, '@audit', selected=self.selected)
        self.assertEqual(self.q.pending()[0]['price'], '150,00')
        row = self.ledger.db.execute('SELECT state,external_id,payload FROM deliveries').fetchone()
        self.assertEqual(row[:2], ('SENT','77')); self.assertEqual(json.loads(row[2])['price'], '100,00')
        self.assertEqual(self.ledger.db.execute('SELECT cents FROM price_history').fetchone()[0], 10000)
        self.assertIsNone(self.ledger.reserve(self.offer['product_id'], offer=self.q.pending()[0], channel='@audit'))

    def test_finish_removes_only_selected_origin_and_not_radar_same_product(self):
        other = dict(self.offer, chat_id=-2)
        self.q.replace_capture([other]); self.i.enqueue([dict(self.offer, source='shopee_api')])
        day = self.reserve(); self.assertTrue(self.authorize(day))
        self.ledger.finish(self.offer['product_id'], day, 77, self.offer, '@audit', selected=self.selected)
        self.assertEqual([o['chat_id'] for o in self.q.pending()], [-2])
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM radar_queue').fetchone()[0], 1)

    def test_old_discard_cannot_remove_replacement_or_other_origin(self):
        self.edit(price='150,00'); self.q.replace_capture([dict(self.offer, chat_id=-2)])
        self.assertEqual(self.q.discard_selected(self.selected), 0)
        self.assertEqual(len(self.q.pending()), 2)

    def test_old_backoff_cannot_replace_or_clear_new_backoff(self):
        backoff = PublisherBackoff(self.ledger.db)
        old = backoff.for_selection(self.selected); old.failure('', '', 'OLD')
        self.edit(price='150,00')
        selected = self.q.pending(include_selection=True)[0]['_queue_selection']
        new = backoff.for_selection(selected); new.failure('', '', 'NEW')
        old.clear(''); self.assertTrue(old.failure('', '', 'OLD')['stale'])
        self.assertEqual(self.ledger.db.execute('SELECT revision,reason FROM publisher_retry').fetchone(),
                         (selected['digest'], 'NEW'))

    def test_backoff_is_separate_for_two_origins_of_same_product(self):
        self.q.replace_capture([dict(self.offer, chat_id=-2)])
        tokens = [o['_queue_selection'] for o in self.q.pending(include_selection=True)]
        b = PublisherBackoff(self.ledger.db)
        for selected in tokens: b.for_selection(selected).failure('', '', 'WAIT')
        b.for_selection(tokens[0]).clear('')
        self.assertGreater(b.for_selection(tokens[1]).remaining('', ''), 0)

    def test_existing_legacy_retry_is_respected_without_being_erased(self):
        b = PublisherBackoff(self.ledger.db)
        b.failure(self.offer['product_id'], offer_revision(self.offer), 'WAIT')
        scoped = b.for_selection(self.selected)
        self.assertGreater(scoped.remaining('', ''), 0)
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM publisher_retry').fetchone()[0], 1)

    def test_confirmation_is_idempotent_and_conflicting_id_is_refused(self):
        day = self.reserve(); self.assertTrue(self.authorize(day))
        self.ledger.finish(self.offer['product_id'], day, 77, self.offer, '@audit', selected=self.selected)
        self.ledger.finish(self.offer['product_id'], day, 77, self.offer, '@audit', selected=self.selected)
        with self.assertRaises(ValueError):
            self.ledger.finish(self.offer['product_id'], day, 78, self.offer, '@audit', selected=self.selected)
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM price_history').fetchone()[0], 1)

    def test_rejected_authorization_does_not_regress_sent_or_uncertain(self):
        for state in ['sent','uncertain','sending']:
            with self.subTest(state=state):
                self.ledger.db.execute('INSERT OR REPLACE INTO posts(product,day,status,message_id) VALUES(?,?,?,77)',
                                       (self.offer['product_id'],'day',state))
                self.ledger.db.execute('INSERT OR REPLACE INTO publication_selections VALUES(?,?,?)',
                                       (self.offer['product_id'],'day',selection_id(self.selected)))
                self.ledger.db.commit()
                self.edit(price='150,00')
                self.assertFalse(self.authorize('day'))
                self.assertEqual(self.ledger.db.execute('SELECT status FROM posts').fetchone()[0], state)
                self.offer=self.q.pending()[0]

    def test_expired_selection_is_not_authorized(self):
        day = self.reserve()
        self.q.db.execute('UPDATE captured_queue SET expires_at=0'); self.q.db.commit()
        self.assertFalse(self.authorize(day))

    def test_failure_during_finish_rolls_back_posts_delivery_history_and_ack(self):
        day = self.reserve(); self.assertTrue(self.authorize(day))
        self.ledger.db.execute("CREATE TRIGGER fail BEFORE INSERT ON deliveries BEGIN SELECT RAISE(ABORT,'injected'); END")
        self.ledger.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.ledger.finish(self.offer['product_id'],day,77,self.offer,'@audit',selected=self.selected)
        self.assertEqual(self.ledger.db.execute('SELECT status FROM posts').fetchone()[0], 'sending')
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM deliveries').fetchone()[0], 0)
        self.assertEqual(len(self.q.pending()), 1)

    def test_eight_independent_connections_authorize_exactly_once(self):
        day = self.reserve(); barrier = threading.Barrier(8)
        def worker(_):
            ledger = Ledger(self.path)
            try:
                barrier.wait()
                return ledger.mark_sending(self.offer['product_id'],day,selected=self.selected)
            finally: ledger.db.close()
        with ThreadPoolExecutor(max_workers=8) as pool: results = list(pool.map(worker, range(8)))
        self.assertEqual(sum(results), 1)

    def test_restart_keeps_new_revision_and_blocks_authorized_uncertain_send(self):
        day = self.reserve(); self.assertTrue(self.authorize(day)); self.edit(price='150,00')
        ledger = Ledger(self.path)
        try:
            self.assertEqual(ledger.reconcile_reservations()['moved_to_uncertain'], 1)
            self.assertIsNone(ledger.reserve(self.offer['product_id'], offer=self.q.pending()[0]))
            self.assertEqual(self.q.pending()[0]['price'], '150,00')
        finally: ledger.db.close()

    def test_legacy_schema_migration_preserves_confirmed_posts_and_adds_only_binding_table(self):
        path=self.root/'legacy.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE posts(product TEXT,day TEXT,status TEXT,message_id INTEGER, PRIMARY KEY(product,day))')
            db.execute("INSERT INTO posts VALUES ('LEGACY','2026-10-01','sent',999)")
        ledger=Ledger(path)
        try:
            self.assertEqual(ledger.db.execute('SELECT product,day,status,message_id FROM posts').fetchall(),
                             [('LEGACY','2026-10-01','sent',999)])
            self.assertEqual(ledger.db.execute('SELECT count(*) FROM publication_selections').fetchone()[0],0)
            self.assertEqual(ledger.db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        finally:ledger.db.close()

    def test_binding_mismatch_cannot_cancel_someone_elses_reserved_selection(self):
        day=self.reserve();self.edit(price='150,00')
        new=self.q.pending(include_selection=True)[0]['_queue_selection']
        self.ledger.db.execute('UPDATE publication_selections SET selection=?',(selection_id(new),))
        self.ledger.db.commit()
        self.assertFalse(self.authorize(day))
        self.assertEqual(self.ledger.cancel_reserved_selection(self.offer['product_id'],day,self.selected),0)
        self.assertEqual(self.ledger.db.execute('SELECT status FROM posts').fetchone()[0],'reserved')

    def test_stale_known_failure_cannot_release_another_reserved_or_sending_selection(self):
        day=self.reserve(); self.edit(price='150,00')
        new=self.q.pending(include_selection=True)[0]['_queue_selection']
        # Uma conexão independente representa a reserva substituta de uma lease antiga.
        with sqlite3.connect(self.path) as writer:
            writer.execute('UPDATE publication_selections SET selection=?',(selection_id(new),))
        for state in ('reserved','sending','uncertain','sent'):
            with self.subTest(state=state):
                with sqlite3.connect(self.path) as writer:
                    writer.execute('UPDATE posts SET status=?',(state,))
                before=self.ledger.db.execute('SELECT * FROM posts').fetchall()
                self.assertEqual(self.ledger.release(self.offer['product_id'],day,selected=self.selected),0)
                self.assertEqual(self.ledger.db.execute('SELECT * FROM posts').fetchall(),before)
                self.assertEqual(self.q.pending()[0]['price'],'150,00')

    def test_uncertain_transition_belongs_to_authorized_binding_not_new_queue_revision(self):
        day=self.reserve(); self.assertTrue(self.authorize(day)); self.edit(price='150,00')
        new=self.q.pending(include_selection=True)[0]['_queue_selection']
        self.assertFalse(self.ledger.mark_uncertain(self.offer['product_id'],day,
            offer=self.q.pending()[0],channel='@audit',selected=new))
        self.assertEqual(self.ledger.db.execute('SELECT status FROM posts').fetchone()[0],'sending')
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)
        self.assertTrue(self.ledger.mark_uncertain(self.offer['product_id'],day,
            offer=self.offer,channel='@audit',selected=self.selected))
        state,payload=self.ledger.db.execute('SELECT state,payload FROM deliveries').fetchone()
        self.assertEqual(state,'UNCERTAIN');self.assertEqual(json.loads(payload)['price'],self.offer['price'])
        self.assertEqual(self.q.pending()[0]['price'],'150,00')
        self.assertEqual(self.ledger.release(self.offer['product_id'],day,selected=self.selected),0)

    def test_stale_metrics_do_not_overwrite_edited_source(self):
        metrics=SourceMetrics(self.path); metrics.record_offer(self.offer, 'CAPTADA')
        new=self.edit(price='150,00',name='Nova mensagem')
        metrics.record_offer(new,'CAPTADA')
        self.assertFalse(metrics.record_offer(self.offer,'REJEITADA','OLD',selected=self.selected))
        self.assertFalse(metrics.record_offer(self.offer,'PUBLICADA',published_message_id=77,selected=self.selected))
        self.assertEqual(self.ledger.db.execute('SELECT status,published FROM source_messages').fetchone(),('CAPTADA',0))


class PublisherRevisionTests(RevisionFixture):
    # Mantém os testes da persistência na classe acima, sem duplicá-los aqui.
    def run_publisher(self, *, gate_hook=None, sender_hook=None, reserve_hook=None, reader_hook=None, mode='shopee'):
        with ExitStack() as stack:
            if mode == 'kabum':
                self.q.db.execute('DELETE FROM captured_queue'); self.q.db.commit()
                catalog=kabum.init_db(self.root/'kabum_historico.sqlite3'); stack.callback(catalog.close)
                kabum.ingest(feed({'21':'100.00'}),catalog)
                kabum.ingest(feed({'21':'80.00'}),catalog)
                self.offer=kabum.production_candidates(catalog,limit=None)[0]
                self.i.enqueue([self.offer])
                ka=KabumAffiliate(self.root/'kabum_historico.sqlite3','3106767')
                stack.enter_context(patch.object(publisher.KabumAffiliate,'from_env',return_value=ka))
            else:
                stack.enter_context(patch.object(publisher.KabumAffiliate,'from_env',side_effect=publisher.AffiliateError('disabled')))
            if mode == 'ml':
                self.q.db.execute('DELETE FROM captured_queue'); self.q.db.commit()
                self.offer=dict(self.offer,kind='ml_offer_pending',store='Mercado Livre',
                    product_id=manual.pending_key('https://meli.la/fixture'),url='https://meli.la/fixture')
                self.q.replace_capture([self.offer])
                client=Mock(cookie='fake',csrf='fake',tag='fake')
                client.prepare.side_effect=lambda o:dict(o,affiliate_generated=True,affiliate_url='https://meli.la/audit')
                stack.enter_context(patch.object(publisher.MercadoLivreAffiliate,'from_env',return_value=client))
                reader=Mock()
                reader.read.side_effect=lambda o,**kw:dict(o,kind='ml_offer',product_id='MercadoLivre:123',
                    url=ml_offer()['url'],original_url='https://meli.la/fixture',name='Lido',price='100,00',price_from=False)
                if reader_hook:reader.read.side_effect=reader_hook
                stack.enter_context(patch('mercadolivre_auto.AutoReader',return_value=reader))
            else:
                stack.enter_context(patch.object(publisher.MercadoLivreAffiliate,'from_env',side_effect=publisher.AffiliateError('disabled')))
            client=Mock();client.prepare.side_effect=lambda o:dict(o,affiliate_generated=True,
                                  affiliate_url='https://s.shopee.com.br/audit')
            stack.enter_context(patch.object(publisher.ShopeeAffiliate,'from_env',return_value=client))
            stack.enter_context(patch.object(publisher.AmazonCreators,'from_env',side_effect=publisher.AffiliateError('disabled')))
            stack.enter_context(patch.object(publisher,'BASE',self.root))
            stack.enter_context(patch.object(publisher,'Ledger',return_value=self.ledger))
            if gate_hook:
                record=PrePublicationGate._record
                def hook(gate,offer,status,reason,*args,**kwargs):
                    if status in ('APROVADA','ATUALIZADA'):gate_hook()
                    return record(gate,offer,status,reason,*args,**kwargs)
                stack.enter_context(patch.object(PrePublicationGate,'_record',hook))
            if reserve_hook:
                reserve=self.ledger.reserve
                def reserve_then_edit(*args,**kwargs):
                    day=reserve(*args,**kwargs);reserve_hook();return day
                stack.enter_context(patch.object(self.ledger,'reserve',side_effect=reserve_then_edit))
            sender=stack.enter_context(patch.object(publisher,'send',side_effect=sender_hook or (lambda *a:(77,0))))
            stack.enter_context(patch.object(publisher.ShadowDistribution,'mirror_success',return_value={}))
            get=stack.enter_context(patch('requests.get',side_effect=AssertionError('network forbidden')))
            post=stack.enter_context(patch('requests.post',side_effect=AssertionError('network forbidden')))
            stack.enter_context(patch.object(publisher.time,'sleep',side_effect=KeyboardInterrupt))
            stack.enter_context(patch.dict(os.environ,{'TELEGRAM_TOKEN':'fake','TELEGRAM_CANAL':'@audit','RADAR_DESTINOS_SHADOW':''}))
            stack.enter_context(patch('builtins.print'))
            with self.assertRaises(KeyboardInterrupt):publisher.run_publisher(SimpleNamespace(simular=False),Mock())
            get.assert_not_called();post.assert_not_called()
            return sender

    def test_live_edit_during_old_gate_sends_nothing_and_preserves_new(self):
        sender=self.run_publisher(gate_hook=lambda:self.edit(price='150,00'))
        sender.assert_not_called();self.assertEqual(self.q.pending()[0]['price'],'150,00')

    def test_ml_edit_during_corrected_read_gate_sends_nothing(self):
        sender=self.run_publisher(mode='ml',gate_hook=lambda:self.edit(price='150,00'))
        sender.assert_not_called();self.assertEqual(self.q.pending()[0]['price'],'150,00')

    def test_gate_failure_of_old_revision_cannot_backoff_the_new(self):
        def fail():
            self.edit(price='150,00')
            raise GateReject('VALIDACAO_INDISPONIVEL','injected')
        self.run_publisher(gate_hook=fail).assert_not_called()
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM publisher_retry').fetchone()[0],0)
        self.assertEqual(self.q.pending()[0]['price'],'150,00')

    def test_ml_read_failure_of_old_revision_cannot_quarantine_new(self):
        def fail(*args,**kwargs):
            self.edit(price='150,00')
            raise publisher.AffiliateError('HTTP 403 injected')
        self.run_publisher(mode='ml',reader_hook=fail).assert_not_called()
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM ml_resolution_failures').fetchone()[0],0)
        self.assertEqual(self.q.pending()[0]['price'],'150,00')

    def test_reader_cache_does_not_reuse_old_coupon_or_native_revision(self):
        from mercadolivre_auto import AutoReader
        reader=AutoReader();self.addCleanup(reader.close)
        old=dict(ml_offer(),chat_id=-1,message_id=7,coupon='OLD',capture_digest='old')
        new=dict(old,coupon='NEW',capture_digest='new')
        with patch('mercadolivre_auto.enrich',side_effect=lambda o:dict(o)) as read:
            self.assertEqual(reader.read(old,blocking=True)['coupon'],'OLD')
            self.assertEqual(reader.read(new,blocking=True)['coupon'],'NEW')
            self.assertEqual(read.call_count,2)

    def change_catalog(self, enqueue=False):
        with sqlite3.connect(self.root/'kabum_historico.sqlite3') as db:
            db.execute("UPDATE kabum_products SET price_cents=15000,last_seen=? WHERE product_id='21'",(datetime.now(timezone.utc).timestamp(),))
        if enqueue:
            with sqlite3.connect(self.path) as db:Intelligence(db).enqueue([dict(self.offer,price='150,00')])

    def test_kabum_catalog_changed_before_enqueue_blocks_old_gate(self):
        sender=self.run_publisher(mode='kabum',gate_hook=self.change_catalog)
        sender.assert_not_called()
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM posts').fetchone()[0],0)
        self.assertEqual(json.loads(self.ledger.db.execute('SELECT payload FROM radar_queue').fetchone()[0])['price'],'80,00')
        self.i.enqueue([dict(self.offer,price='150,00')])
        self.assertEqual(json.loads(self.ledger.db.execute('SELECT payload FROM radar_queue').fetchone()[0])['price'],'150,00')

    def test_kabum_catalog_and_queue_changed_during_gate_are_preserved(self):
        sender=self.run_publisher(mode='kabum',gate_hook=lambda:self.change_catalog(enqueue=True))
        sender.assert_not_called()
        self.assertEqual(json.loads(self.ledger.db.execute('SELECT payload FROM radar_queue').fetchone()[0])['price'],'150,00')

    def test_edit_between_real_reserve_and_sending_cancels_reserved_only(self):
        sender=self.run_publisher(reserve_hook=lambda:self.edit(price='150,00'))
        sender.assert_not_called();self.assertEqual(self.q.pending()[0]['price'],'150,00')
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM posts').fetchone()[0],0)

    def test_live_edit_during_send_keeps_confirmation_and_new_queue(self):
        def sender(*args):self.edit(price='150,00');return 77,0
        self.run_publisher(sender_hook=sender).assert_called_once()
        self.assertEqual(self.q.pending()[0]['price'],'150,00')
        self.assertEqual(self.ledger.db.execute('SELECT status,message_id FROM posts').fetchone(),('sent',77))
        self.assertEqual(json.loads(self.ledger.db.execute('SELECT payload FROM deliveries').fetchone()[0])['price'],'100,00')

    def test_permanent_error_after_edit_cannot_discard_new_revision(self):
        def sender(*args):
            self.edit(price='150,00');raise TelegramSendError('permanent','fixture',error_code=400)
        self.run_publisher(sender_hook=sender)
        self.assertEqual(self.q.pending()[0]['price'],'150,00')

    def test_uncertain_error_after_edit_keeps_new_and_blocks_resend(self):
        def sender(*args):
            self.edit(price='150,00');raise TelegramSendError('uncertain','fixture')
        self.run_publisher(sender_hook=sender)
        self.assertEqual(self.q.pending()[0]['price'],'150,00')
        self.assertEqual(self.ledger.db.execute('SELECT status FROM posts').fetchone()[0],'uncertain')

    def test_exception_after_edit_keeps_new_and_uncertain(self):
        def sender(*args):self.edit(price='150,00');raise RuntimeError('injected')
        self.run_publisher(sender_hook=sender)
        self.assertEqual(self.q.pending()[0]['price'],'150,00')
        self.assertEqual(self.ledger.db.execute('SELECT status FROM posts').fetchone()[0],'uncertain')

    def test_cancel_during_send_leaves_sending_for_uncertain_recovery(self):
        def sender(*args):self.edit(price='150,00');raise KeyboardInterrupt
        self.run_publisher(sender_hook=sender)
        self.assertEqual(self.ledger.db.execute('SELECT status FROM posts').fetchone()[0],'sending')
        self.ledger.reconcile_reservations()
        self.assertEqual(self.ledger.db.execute('SELECT status FROM posts').fetchone()[0],'uncertain')
        self.assertEqual(self.q.pending()[0]['price'],'150,00')


class CatalogConcurrencyTests(RevisionFixture):
    def prepare_catalog(self):
        self.catalog_path=self.root/'kabum_historico.sqlite3'
        db=kabum.init_db(self.catalog_path)
        try:
            kabum.ingest(feed({'21':'100.00'}),db)
            proof={'product_id':'21','digest':catalog_revision(db,'21')}
        finally:db.close()
        self.offer=dict(self.offer,product_id='KaBuM:21',store='KaBuM',source='kabum_feed')
        self.i.enqueue([self.offer])
        self.selected=self.i.pending(include_selection=True)[0]['_queue_selection']
        return proof

    def test_catalog_commit_before_authorization_without_enqueue_is_detected(self):
        proof=self.prepare_catalog();day=self.reserve()
        writer=sqlite3.connect(self.catalog_path);self.addCleanup(writer.close)
        writer.execute('BEGIN IMMEDIATE')
        writer.execute("UPDATE kabum_products SET price_cents=15000 WHERE product_id='21'")
        started=threading.Event();done=threading.Event();result=[];errors=[]
        def consumer():
            ledger=Ledger(self.path)
            try:
                started.set()
                result.append(ledger.mark_sending(self.offer['product_id'],day,selected=self.selected,
                              catalog_path=self.catalog_path,catalog_proof=proof))
            except BaseException as exc:errors.append(exc)
            finally:ledger.db.close();done.set()
        thread=threading.Thread(target=consumer);thread.start()
        self.assertTrue(started.wait(5));self.assertFalse(done.wait(.05))
        writer.commit();thread.join(5)
        self.assertFalse(thread.is_alive());self.assertFalse(errors);self.assertEqual(result,[False])
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM posts').fetchone()[0],0)
        self.assertEqual(json.loads(self.ledger.db.execute('SELECT payload FROM radar_queue').fetchone()[0])['price'],'100,00')

    def test_authorization_commit_precedes_waiting_catalog_writer(self):
        proof=self.prepare_catalog();day=self.reserve()
        authorized=threading.Event();writer_started=threading.Event();writer_done=threading.Event();errors=[]
        def writer():
            try:
                self.assertTrue(authorized.wait(5))
                with sqlite3.connect(self.catalog_path,timeout=5) as db:
                    writer_started.set();db.execute('BEGIN IMMEDIATE')
                    db.execute("UPDATE kabum_products SET price_cents=15000 WHERE product_id='21'")
                with sqlite3.connect(self.path) as db:Intelligence(db).enqueue([dict(self.offer,price='150,00')])
            except BaseException as exc:errors.append(exc)
            finally:writer_done.set()
        def inside_authorization():
            authorized.set();self.assertTrue(writer_started.wait(5))
            self.assertFalse(writer_done.wait(.05));return 1
        self.ledger.db.create_function('inside_authorization',0,inside_authorization)
        self.ledger.db.execute("CREATE TRIGGER fence BEFORE UPDATE OF status ON posts WHEN NEW.status='sending' BEGIN SELECT inside_authorization(); END")
        self.ledger.db.commit()
        thread=threading.Thread(target=writer);thread.start()
        self.assertTrue(self.authorize(day,catalog_path=self.catalog_path,catalog_proof=proof))
        thread.join(5);self.assertFalse(thread.is_alive());self.assertFalse(errors)
        self.ledger.finish(self.offer['product_id'],day,77,self.offer,'@audit',selected=self.selected)
        self.assertEqual(self.ledger.db.execute('SELECT status FROM posts').fetchone()[0],'sent')
        self.assertEqual(json.loads(self.ledger.db.execute('SELECT payload FROM radar_queue').fetchone()[0])['price'],'150,00')

    def test_exception_inside_authorization_rolls_back_and_releases_both_locks(self):
        proof=self.prepare_catalog();day=self.reserve()
        self.ledger.db.execute("CREATE TRIGGER fail BEFORE UPDATE ON posts BEGIN SELECT RAISE(ABORT,'injected'); END")
        self.ledger.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):self.authorize(day,catalog_path=self.catalog_path,catalog_proof=proof)
        self.assertEqual(self.ledger.db.execute('SELECT status FROM posts').fetchone()[0],'reserved')
        with sqlite3.connect(self.catalog_path,timeout=.1) as db:
            db.execute("UPDATE kabum_products SET price_cents=15000 WHERE product_id='21'")
        with sqlite3.connect(self.path,timeout=.1) as db:
            db.execute('UPDATE radar_queue SET refreshed=refreshed+1')


CRASH_CHILD = '''
import os,sys,json
from pathlib import Path
from ofertas_core import Ledger
from fila_ofertas_sqlite import CapturedOfferQueue
phase,filename=sys.argv[1:]
ledger=Ledger(filename);queue=CapturedOfferQueue(filename)
offer=queue.pending(include_selection=True)[0];selected=offer.pop('_queue_selection')
day=ledger.reserve(offer['product_id'],offer=offer,channel='@audit',selected=selected)
if phase=='reserved':os._exit(77)
assert ledger.mark_sending(offer['product_id'],day,selected=selected)
if phase=='sending':os._exit(77)
Path(filename+'.accepted').write_text('77')
if phase=='accepted':os._exit(77)
if phase=='during_finish':
 ledger.db.create_function('crash_now',0,lambda:os._exit(77))
 ledger.db.execute('CREATE TRIGGER crash BEFORE INSERT ON deliveries BEGIN SELECT crash_now(); END')
 ledger.db.commit()
ledger.finish(offer['product_id'],day,77,offer,'@audit',selected=selected)
os._exit(77)
'''


class RevisionCrashTests(RevisionFixture):
    def crash(self, phase):
        p=subprocess.run([sys.executable,'-c',CRASH_CHILD,phase,str(self.path)],
                          cwd=ROOT,capture_output=True,text=True,timeout=15)
        self.assertEqual(p.returncode,77,p.stderr)

    def test_crash_reserved_then_native_edit_allows_only_new_selection(self):
        self.crash('reserved');self.edit(price='150,00')
        self.assertEqual(self.ledger.reconcile_reservations()['released_abandoned_reserved'],1)
        self.assertIsNone(self.reserve())
        new=self.q.pending(include_selection=True)[0];selected=new.pop('_queue_selection')
        self.assertIsNotNone(self.ledger.reserve(new['product_id'],offer=new,selected=selected))

    def test_crash_sending_or_remote_accepted_keeps_new_revision_and_blocks_resend(self):
        for phase in ['sending','accepted','during_finish']:
            with self.subTest(phase=phase):
                self.crash(phase);self.edit(price='150,00')
                self.assertEqual(self.ledger.reconcile_reservations()['moved_to_uncertain'],1)
                self.assertIsNone(self.reserve());self.assertEqual(self.q.pending()[0]['price'],'150,00')
                self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)
                # Limpa apenas a fixture para reproduzir a próxima fronteira.
                self.ledger.db.execute('DELETE FROM posts');self.ledger.db.commit()
                self.offer=self.q.pending()[0]
                self.selected=self.q.pending(include_selection=True)[0]['_queue_selection']

    def test_crash_after_atomic_finish_keeps_sent_and_never_resends(self):
        self.crash('finished')
        self.ledger.reconcile_reservations()
        self.assertEqual(self.ledger.db.execute('SELECT status,message_id FROM posts').fetchone(),('sent',77))
        self.assertEqual(self.q.pending(),[])
        self.assertEqual(self.ledger.db.execute('SELECT state,external_id FROM deliveries').fetchone(),('SENT','77'))
        self.assertIsNone(self.ledger.reserve(self.offer['product_id'],offer=self.offer,channel='@audit'))
