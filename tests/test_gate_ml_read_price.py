"""Preço validado vem do leitor, não do timestamp de enriquecimento."""
import sqlite3
import tempfile
import time
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bot_ofertas_revisao as publisher
import mercadolivre_manual as manual
from mercadolivre_auto import AutoReader
from ofertas_core import Ledger
from prepublicacao import GateReject, PrePublicationGate
from fila_ofertas_sqlite import CapturedOfferQueue
from tests.test_prepublicacao import ml_offer


class MLReadPriceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE posts(product TEXT,day TEXT,status TEXT,message_id INTEGER)')
        self.reader = AutoReader()
        self.addCleanup(self.reader.close)
        self.gate = PrePublicationGate(self.temp.name, self.db, ml_reader=self.reader)
        self.original = ml_offer()
        self.prepared = dict(self.original, name='Nome herdado',
            api_image='https://http2.mlstatic.com/capture.jpg', auto_fetched_at=time.time())

    def page(self, price='100,00', **kwargs):
        return dict(name='Nome lido', price=price, price_condition='', price_from=False,
                    resolved_url=self.original['url'],
                    api_image='https://http2.mlstatic.com/current.jpg',
                    auto_fetched_at=time.time(), **kwargs)

    def validate_page(self, page):
        with patch('mercadolivre_auto.fetch_http', return_value=page):
            return self.gate.validate(self.original, self.prepared, '@audit')

    def reject_page(self, page, reason):
        with self.assertRaises(GateReject) as caught:
            self.validate_page(page)
        self.assertEqual(caught.exception.reason, reason)

    def test_price_higher_blocks_despite_recent_enrichment_timestamp(self):
        self.reject_page(self.page('150,00'), 'PRECO_AUMENTOU')

    def test_equal_price_is_strong_validation(self):
        result = self.validate_page(self.page())
        self.assertEqual(result['price'], '100,00')
        self.assertEqual(result['prepublication_validation'], 'VALIDACAO_FORTE')
        self.assertEqual(result['name'], 'Nome lido')

    def test_lower_price_is_the_price_to_publish(self):
        result = self.validate_page(self.page('90,00'))
        self.assertEqual(result['price'], '90,00')
        self.assertEqual(result['prepublication_validation'], 'PRECO_ATUALIZADO_MENOR')

    def test_missing_price_cannot_inherit_capture_price(self):
        for price in (None, '', '0,00', '-1,00'):
            with self.subTest(price=price):
                self.reader.cache.clear()
                self.reject_page(self.page(price), 'PRECO_NAO_CONFIRMADO')

    def test_range_is_blocked(self):
        page = self.page('90,00')
        page['price_from'] = True
        self.reject_page(page, 'PRECO_VARIANTE_AMBIGUO')

    def test_complete_capture_does_not_supply_the_validation_price(self):
        reader = Mock()
        reader.read.return_value = dict(self.original, price='90,00')
        self.gate.ml_reader = reader
        result = self.gate.validate(self.original, self.prepared, '@audit')
        self.assertEqual(result['price'], '90,00')
        probe = reader.read.call_args.args[0]
        for field in ('price', 'price_condition', 'price_from', 'name', 'image', 'api_image'):
            self.assertNotIn(field, probe)
        self.assertTrue(reader.read.call_args.kwargs['blocking'])

    def test_original_reproduction_with_real_enrich_preserving_old_price(self):
        original = dict(self.original, name='')
        with patch('mercadolivre_auto.fetch_http', return_value=self.page('150,00')):
            enriched = self.reader.read(original, blocking=True)
            self.assertEqual(enriched['price'], '100,00')
            self.assertEqual(enriched['name'], 'Nome lido')
            prepared = dict(enriched, affiliate_generated=True, affiliate_url='https://meli.la/audit')
            with self.assertRaises(GateReject) as caught:
                self.gate.validate(original, prepared, '@audit')
        self.assertEqual(caught.exception.reason, 'PRECO_AUMENTOU')

    def test_partial_reader_result_with_name_image_but_no_price_is_blocked(self):
        reader = Mock()
        reader.read.return_value = dict(product_id=self.original['product_id'],
            resolved_url=self.original['url'], name='Título novo',
            api_image='https://http2.mlstatic.com/new.jpg', auto_fetched_at=time.time())
        self.gate.ml_reader = reader
        with self.assertRaises(GateReject) as caught:
            self.gate.validate(self.original, self.prepared, '@audit')
        self.assertEqual(caught.exception.reason, 'PRECO_NAO_CONFIRMADO')

    def test_timeout_is_unavailable_not_permission_to_publish(self):
        from shopee_afiliados import AffiliateError
        with patch('mercadolivre_auto.fetch_http', side_effect=AffiliateError('timeout')), \
             patch('mercadolivre_auto.fetch_browser', side_effect=AffiliateError('timeout')):
            with self.assertRaises(GateReject) as caught:
                self.gate.validate(self.original, self.prepared, '@audit')
        self.assertEqual(caught.exception.reason, 'VALIDACAO_INDISPONIVEL')

    def test_incomplete_reader_response_is_unavailable(self):
        self.gate.ml_reader = Mock()
        for response in (None, {}):
            with self.subTest(response=response):
                self.gate.ml_reader.read.return_value = response
                with self.assertRaises(GateReject) as caught:
                    self.gate.validate(self.original, self.prepared, '@audit')
                self.assertEqual(caught.exception.reason, 'VALIDACAO_INDISPONIVEL')

    def test_blocking_cache_reuses_only_read_price_and_expires_after_sixty_seconds(self):
        clock = [1000.0]
        with patch('mercadolivre_auto.time.monotonic', side_effect=lambda: clock[0]), \
             patch('mercadolivre_auto.fetch_http', return_value=self.page('90,00')) as fetch:
            first = self.gate.validate(self.original, self.prepared, '@audit')
            self.original['price'] = '95,00'  # Nova revisão não injeta seu preço no cache.
            self.prepared.update(price='95,00', source_revision_at='new')
            second = self.gate.validate(self.original, self.prepared, '@audit')
            self.assertEqual((first['price'], second['price']), ('90,00', '90,00'))
            self.assertEqual(fetch.call_count, 1)
            clock[0] += 61
            self.gate.validate(self.original, self.prepared, '@audit')
            self.assertEqual(fetch.call_count, 2)
        self.assertEqual(self.reader.GATE_CACHE_SECONDS, 60)
        self.assertEqual(self.reader.CACHE_SECONDS, 300)

    def test_cached_higher_price_still_blocks(self):
        with patch('mercadolivre_auto.fetch_http', return_value=self.page('150,00')) as fetch:
            for _ in range(2):
                with self.assertRaises(GateReject) as caught:
                    self.gate.validate(self.original, self.prepared, '@audit')
                self.assertEqual(caught.exception.reason, 'PRECO_AUMENTOU')
            self.assertEqual(fetch.call_count, 1)

    def test_native_variant_url_does_not_share_read_cache(self):
        with patch('mercadolivre_auto.fetch_http', side_effect=[self.page('90,00'), self.page('80,00')]) as fetch:
            self.original['url'] += '?variation=1'
            self.prepared['url'] = self.original['url']
            first = self.gate.validate(self.original, self.prepared, '@audit')
            self.original['url'] = self.original['url'].replace('variation=1', 'variation=2')
            self.prepared['url'] = self.original['url']
            second = self.gate.validate(self.original, self.prepared, '@audit')
            self.assertEqual((first['price'], second['price']), ('90,00', '80,00'))
            self.assertEqual(fetch.call_count, 2)

    def test_different_product_returned_by_reader_blocks(self):
        page = self.page('90,00')
        page['resolved_url'] = 'https://produto.mercadolivre.com.br/MLB-456-_JM'
        self.reject_page(page, 'PRODUTO_INCONSISTENTE')

    def test_recent_manual_without_automatic_read_keeps_own_policy(self):
        offer = dict(self.original, kind='ml_manual_offer', url='https://meli.la/manual',
            product_id=manual.key('https://meli.la/manual'), chat_id='-1',
            manual_link_preserved=True, affiliate_generated=False, affiliate_url='https://meli.la/manual')
        self.gate.ml_reader = Mock()
        result = self.gate.validate(offer, dict(offer), '@audit')
        self.assertEqual(result['price'], '100,00')
        self.assertEqual(result['prepublication_validation'], 'PRECO_ORIGEM_RECENTE')
        self.gate.ml_reader.read.assert_not_called()

    def test_recent_manual_mirror_preserves_template_without_forcing_read(self):
        offer = dict(self.original, kind='ml_manual_offer', url='https://meli.la/manual',
            product_id=manual.key('https://meli.la/manual'), manual_link_preserved=True,
            affiliate_generated=False, affiliate_url='https://meli.la/manual', publish_mode='mirror',
            mirror_template='Texto original R$100 com condições e cupom')
        self.gate.ml_reader = Mock()
        result = self.gate.validate(offer, dict(offer), '@audit')
        self.assertEqual(result['mirror_template'], offer['mirror_template'])
        self.gate.ml_reader.read.assert_not_called()


class MLPublisherPriceTests(unittest.TestCase):
    def test_real_publisher_blocks_before_reserve_send_and_shadow(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            ledger = Ledger(root / 'publicacoes.sqlite3')
            stack.callback(ledger.db.close)
            original = dict(ml_offer(), name='', chat_id=-1, message_id=1)
            reader = AutoReader()
            stack.callback(reader.close)
            page = dict(name='Produto atual', price='150,00', price_condition='', price_from=False,
                resolved_url=original['url'], api_image='https://http2.mlstatic.com/fixture.jpg',
                auto_fetched_at=time.time())
            stack.enter_context(patch('mercadolivre_auto.fetch_http', return_value=page))
            reader.read(original, blocking=True)  # Simula job de preparação já concluído.
            stack.enter_context(patch('mercadolivre_auto.AutoReader', return_value=reader))
            queue = CapturedOfferQueue(root / 'publicacoes.sqlite3')
            queue.replace_capture([original])
            queue.close()
            affiliate = Mock(cookie='fake', csrf='fake', tag='fake')
            affiliate.prepare.side_effect = lambda o: dict(o, affiliate_generated=True,
                affiliate_url='https://meli.la/audit')
            stack.enter_context(patch.object(publisher, 'BASE', root))
            stack.enter_context(patch.object(publisher, 'Ledger', return_value=ledger))
            stack.enter_context(patch.object(publisher.MercadoLivreAffiliate, 'from_env', return_value=affiliate))
            for factory in (publisher.ShopeeAffiliate, publisher.KabumAffiliate, publisher.AmazonCreators):
                stack.enter_context(patch.object(factory, 'from_env', side_effect=publisher.AffiliateError('disabled locally')))
            stack.enter_context(patch('requests.post', side_effect=AssertionError('unexpected network')))
            stack.enter_context(patch('requests.get', side_effect=AssertionError('unexpected network')))
            reserve = stack.enter_context(patch.object(ledger, 'reserve', wraps=ledger.reserve))
            sending = stack.enter_context(patch.object(ledger, 'mark_sending', wraps=ledger.mark_sending))
            send = stack.enter_context(patch.object(publisher, 'send'))
            shadow = stack.enter_context(patch.object(publisher.ShadowDistribution, 'mirror_success'))
            discard = stack.enter_context(patch.object(publisher.CapturedOfferQueue, 'discard_product'))
            stack.enter_context(patch.object(publisher.time, 'sleep', side_effect=KeyboardInterrupt))
            stack.enter_context(patch.dict('os.environ', {'TELEGRAM_TOKEN':'fake','TELEGRAM_CANAL':'@audit'}))
            stack.enter_context(patch('builtins.print'))
            with self.assertRaises(KeyboardInterrupt):
                publisher.run_publisher(SimpleNamespace(simular=False), Mock())
            self.assertEqual(ledger.db.execute('SELECT reason FROM prepublication_gate').fetchall(), [('PRECO_AUMENTOU',)])
            reserve.assert_not_called()
            sending.assert_not_called()
            send.assert_not_called()
            shadow.assert_not_called()
            discard.assert_not_called()
            self.assertEqual(ledger.db.execute('SELECT count(*) FROM captured_queue').fetchone()[0], 1)
