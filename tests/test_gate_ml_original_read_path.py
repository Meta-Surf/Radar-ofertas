"""A releitura usa a origem que resolve; preço e identidade continuam obrigatórios."""
import sqlite3
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bot_ofertas_revisao as publisher
import mercadolivre_manual as manual
from mercadolivre_auto import AutoReader
from ofertas_core import Ledger
from fila_ofertas_sqlite import CapturedOfferQueue
from prepublicacao import PrePublicationGate, GateReject
from tests.test_prepublicacao import ml_offer
from tests.publisher_fixtures import structured_stock

SHORT='https://meli.la/fixture'


class OriginalMLReadPathTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE posts(product TEXT,day TEXT,status TEXT,message_id INTEGER)')
        self.reader=AutoReader();self.addCleanup(self.reader.close)
        self.gate=PrePublicationGate(self.temp.name,self.db,ml_reader=self.reader)
        self.original=ml_offer()
        self.prepared=dict(self.original,original_url=SHORT,name='Capturado',api_image='https://http2.mlstatic.com/a.jpg')

    def page(self,price='100,00',target=None):
        return dict(name='Atual',price=price,price_condition='',price_from=False,
                    api_image='https://http2.mlstatic.com/read.jpg',
                    resolved_url=target or self.original['url'],auto_fetched_at=time.time(), **structured_stock(target or self.original['url']))

    def validate(self,page):
        def fetch(url):
            if url==SHORT:return page
            raise publisher.AffiliateError('canonical redirect circular')
        with patch('mercadolivre_auto.fetch_http',side_effect=fetch) as http, \
             patch('mercadolivre_auto.fetch_browser',side_effect=publisher.AffiliateError('browser blocked')) as browser:
            result=self.gate.validate(self.original,self.prepared,'@audit')
            self.assertEqual(http.call_args.args[0],SHORT)
            browser.assert_not_called()
            return result

    def test_short_link_revalidated_even_when_canonical_path_cannot_be_read(self):
        result=self.validate(self.page())
        self.assertEqual(result['prepublication_validation'],'VALIDACAO_FORTE')
        self.assertEqual(result['url'],self.original['url'])
        self.assertEqual(result['affiliate_url'],self.prepared['affiliate_url'])

    def test_increased_price_still_blocks(self):
        with self.assertRaises(GateReject) as error:self.validate(self.page('150,00'))
        self.assertEqual(error.exception.reason,'PRECO_AUMENTOU')

    def test_lower_read_price_replaces_captured_price(self):
        self.assertEqual(self.validate(self.page('90,00'))['price'],'90,00')

    def test_missing_and_range_prices_still_block(self):
        for page,reason in [(self.page(None),'PRECO_NAO_CONFIRMADO'),
                            (dict(self.page(),price_from=True),'PRECO_VARIANTE_AMBIGUO')]:
            self.reader.cache.clear()
            with self.assertRaises(GateReject) as error:self.validate(page)
            self.assertEqual(error.exception.reason,reason)

    def test_short_link_resolving_another_product_does_not_authorize(self):
        with self.assertRaises(GateReject) as error:
            self.validate(self.page(target='https://produto.mercadolivre.com.br/MLB-456-_JM'))
        self.assertEqual(error.exception.reason,'PRODUTO_INCONSISTENTE')

    def test_failed_short_read_is_backoff_not_approval(self):
        with patch('mercadolivre_auto.fetch_http',side_effect=publisher.AffiliateError('HTTP 403')), \
             patch('mercadolivre_auto.fetch_browser',side_effect=publisher.AffiliateError('HTTP 403')):
            with self.assertRaises(GateReject) as error:self.gate.validate(self.original,self.prepared,'@audit')
        self.assertEqual(error.exception.reason,'VALIDACAO_INDISPONIVEL')
        self.assertFalse(error.exception.discard)

    def test_direct_offer_without_original_url_keeps_canonical_read(self):
        with patch('mercadolivre_auto.fetch_http',return_value=self.page()) as http:
            self.gate.validate(self.original,self.original,'@audit')
        self.assertEqual(http.call_args.args[0],self.original['url'])

    def test_non_ml_origin_cannot_redirect_the_read_to_an_external_domain(self):
        self.prepared['original_url']='https://evil.invalid/product'
        with patch('mercadolivre_auto.fetch_http',return_value=self.page()) as http:
            self.gate.validate(self.original,self.prepared,'@audit')
        self.assertEqual(http.call_args.args[0],self.original['url'])

    def test_real_publisher_one_success_and_no_duplicate_with_original_path(self):
        root=Path(self.temp.name)
        ledger=Ledger(root/'publicacoes.sqlite3');self.addCleanup(ledger.db.close)
        pending=dict(self.original,kind='ml_offer_pending',product_id=manual.pending_key(SHORT),url=SHORT,
                     chat_id=-1,message_id=7,name='Capturado',api_image='https://http2.mlstatic.com/c.jpg')
        queue=CapturedOfferQueue(root/'publicacoes.sqlite3');queue.replace_capture([pending]);queue.close()
        page=self.page()
        affiliate=Mock(cookie='fake',csrf='fake',tag='fake')
        affiliate.prepare.side_effect=lambda o:dict(o,affiliate_generated=True,affiliate_url='https://meli.la/own')
        with ExitStack() as stack:
            def fetch(url):
                if url==SHORT:return page
                raise publisher.AffiliateError('canonical redirect circular')
            http=stack.enter_context(patch('mercadolivre_auto.fetch_http',side_effect=fetch))
            stack.enter_context(patch('mercadolivre_auto.fetch_browser',side_effect=publisher.AffiliateError('browser blocked')))
            self.reader.read(pending,blocking=True)
            stack.enter_context(patch('mercadolivre_auto.AutoReader',return_value=self.reader))
            stack.enter_context(patch.object(publisher,'BASE',root))
            stack.enter_context(patch.object(publisher,'Ledger',return_value=ledger))
            stack.enter_context(patch.object(publisher.MercadoLivreAffiliate,'from_env',return_value=affiliate))
            for factory in (publisher.ShopeeAffiliate,publisher.KabumAffiliate,publisher.AmazonCreators):
                stack.enter_context(patch.object(factory,'from_env',side_effect=publisher.AffiliateError('disabled')))
            get=stack.enter_context(patch('requests.get',side_effect=AssertionError('network forbidden')))
            post=stack.enter_context(patch('requests.post',side_effect=AssertionError('network forbidden')))
            send=stack.enter_context(patch.object(publisher,'send',return_value=(77,0)))
            stack.enter_context(patch.object(publisher.ShadowDistribution,'mirror_success',return_value={}))
            stack.enter_context(patch.object(publisher.time,'sleep',side_effect=KeyboardInterrupt))
            stack.enter_context(patch.dict('os.environ',{'TELEGRAM_TOKEN':'fake','TELEGRAM_CANAL':'@audit'}))
            stack.enter_context(patch('builtins.print'))
            for _ in range(2):
                with self.assertRaises(KeyboardInterrupt):publisher.run_publisher(SimpleNamespace(simular=False),Mock())
            self.assertEqual(send.call_count,1)
            self.assertEqual(send.call_args.args[2]['price'],'100,00')
            self.assertTrue(all(call.args[0]==SHORT for call in http.call_args_list))
            self.assertEqual(ledger.db.execute('SELECT status,message_id FROM posts').fetchall(),[('sent',77)])
            get.assert_not_called();post.assert_not_called()
