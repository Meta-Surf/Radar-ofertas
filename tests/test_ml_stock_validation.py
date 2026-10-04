"""Disponibilidade ML no Gate, DOM/transportes simulados e SQLite temporário."""
import copy
import json
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import Future
from contextlib import ExitStack
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import bot_ofertas_revisao as publisher
import mercadolivre_auto as auto
import mercadolivre_manual as manual
from fila_ofertas_sqlite import CapturedOfferQueue
from ofertas_core import Ledger
from prepublicacao import PrePublicationGate, GateReject
from shopee_afiliados import AffiliateError
from tests.test_ml_auto import DATA, document, response

DIRECT = 'https://www.mercadolivre.com.br/p/MLB123456'
OTHER = 'https://www.mercadolivre.com.br/p/MLB999999'
SOCIAL = 'https://www.mercadolivre.com.br/social/audit'
IMAGE = 'https://http2.mlstatic.com/audit.webp'


def structured(status='InStock', price='100.00', url=DIRECT):
    data = copy.deepcopy(DATA)
    data.update(url=url, image=IMAGE)
    data['offers'].update(price=price)
    if status is None: data['offers'].pop('availability')
    else: data['offers']['availability'] = 'https://schema.org/' + status
    return document(data)


def offer(**extra):
    return dict(kind='ml_offer', source='telegram', store='Mercado Livre', product_id='MercadoLivre:123456',
        url=DIRECT, original_url=SOCIAL, name='Câmera', price='100,00', price_from=False, api_image=IMAGE,
        source_date=datetime.now(timezone.utc).isoformat(), source_revision_at=datetime.now(timezone.utc).isoformat(), chat_id=-1, message_id=1,
        affiliate_generated=True, affiliate_url='https://meli.la/audit', **extra)


def stock_fields(url=DIRECT):
    return dict(stock_confirmed=True,stock_status='in_stock',stock_source='structured_product',
                stock_product_id=auto.product(url)[0],stock_resolved_url=url)


class StockFixture(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.db = sqlite3.connect(':memory:'); self.stack.callback(self.db.close)
        self.db.execute('CREATE TABLE posts(product TEXT,day TEXT,status TEXT,message_id INTEGER)')
        self.reader = auto.AutoReader(); self.stack.callback(self.reader.close)
        self.gate = PrePublicationGate(self.root,self.db,ml_reader=self.reader)
        self.original = offer()
        self.network = self.stack.enter_context(patch('requests.sessions.Session.request',side_effect=AssertionError('external network forbidden')))

    def fake_browser(self, text=None, card_text='Câmera R$ 100,00', target=DIRECT, redirected=None, status=200):
        module = ModuleType('playwright.sync_api')
        pw,browser,context,page = Mock(),Mock(),Mock(),Mock()
        session=Mock();session.__enter__=Mock(return_value=pw);session.__exit__=Mock(return_value=False)
        module.sync_playwright=Mock(return_value=session);pw.chromium.launch.return_value=browser
        browser.new_context.return_value=context;context.new_page.return_value=page
        page.url=SOCIAL
        def navigate(url,**kwargs):
            page.url = SOCIAL if url==SOCIAL else (redirected or url)
            return SimpleNamespace(status=200 if url==SOCIAL else status)
        page.goto.side_effect=navigate
        page.content.return_value=text if text is not None else structured()
        controls,control=Mock(),Mock();page.locator.return_value=controls
        controls.count.return_value=1;controls.nth.return_value=control;control.inner_text.return_value='Ir para produto'
        control.evaluate.return_value={'href':target,'title':'Câmera','currentPriceText':'R$ 100,00',
                                       'cardText':card_text,'imageCandidates':[IMAGE]}
        self.stack.enter_context(patch.dict(sys.modules,{'playwright.sync_api':module}))
        return page,browser,controls,control

    def gate_reject(self, reason):
        with self.assertRaises(GateReject) as caught: self.gate.validate(self.original,dict(self.original),'@audit')
        self.assertEqual(caught.exception.reason,reason)
        return caught.exception


class StructuredTests(StockFixture):
    def test_declared_other_variant_is_inconsistent(self):
        with self.assertRaises(auto.MLProductInconsistent):
            auto.extract_product(structured(url=DIRECT+'?variation_id=2'),DIRECT+'?variation_id=1')

    def test_negative_stock_not_lost_when_price_or_name_missing(self):
        data=copy.deepcopy(DATA)
        data.pop('name')
        data['offers'].update(availability='https://schema.org/OutOfStock')
        data['offers'].pop('priceCurrency');data['offers'].pop('price')
        with self.assertRaises(auto.MLCommercialUnavailable):auto.extract_product(document(data),DIRECT)

    def test_in_stock_is_explicit_same_product_proof(self):
        result=auto.extract_product(structured(),DIRECT)
        self.assertTrue(auto.confirmed_stock(result,DIRECT))
        self.assertEqual(result['stock_product_id'],'MercadoLivre:123456')

    def test_out_of_stock_specific_commercial_exception(self):
        for state in ('OutOfStock','SoldOut','Discontinued'):
            with self.subTest(state=state),self.assertRaises(auto.MLCommercialUnavailable):
                auto.extract_product(structured(state),DIRECT)

    def test_unknown_is_read_failure_not_commercial(self):
        for state in (None,'Unknown','PreOrder'):
            with self.subTest(state=state),self.assertRaises(AffiliateError) as caught:
                auto.extract_product(structured(state),DIRECT)
            self.assertNotIsInstance(caught.exception,auto.MLCommercialUnavailable)

    def test_http_commercial_refusal_does_not_follow_recommendation(self):
        transport=Mock();transport.get.return_value=response(text=structured('OutOfStock')+'<a href="'+OTHER+'">Outro item</a>')
        with patch.object(auto,'next_destination') as next_url,self.assertRaises(auto.MLCommercialUnavailable):
            auto.fetch_http(DIRECT,transport)
        next_url.assert_not_called();transport.get.assert_called_once()

    def test_native_refusal_never_calls_browser(self):
        with patch.object(auto,'fetch_http',side_effect=lambda url:auto.extract_product(structured('OutOfStock'),DIRECT)), \
             patch.object(auto,'fetch_browser',return_value=auto.extract_product(structured(),DIRECT)) as browser, \
             self.assertRaises(auto.MLCommercialUnavailable): auto.enrich(self.original)
        browser.assert_not_called()

    def test_http_status_alone_is_not_removed_product(self):
        for status in (403,404,429,500):
            transport=Mock();transport.get.return_value=response(status=status)
            with self.subTest(status=status),self.assertRaises(AffiliateError) as caught:auto.fetch_http(DIRECT,transport)
            self.assertNotIsInstance(caught.exception,auto.MLCommercialUnavailable)

    def test_timeout_and_connection_error_are_read_failure(self):
        import requests
        for error in (requests.Timeout(),requests.ConnectionError()):
            transport=Mock();transport.get.side_effect=error
            with self.assertRaises(AffiliateError) as caught:auto.fetch_http(DIRECT,transport)
            self.assertNotIsInstance(caught.exception,auto.MLCommercialUnavailable)

    def test_partial_or_bad_html_not_positive(self):
        for body in ('invalid','<script type="application/ld+json">oops</script>',
                     document({'@type':'Product','name':'Nome','offers':{}})):
            with self.assertRaises(AffiliateError) as caught:auto.extract_product(body,DIRECT)
            self.assertNotIsInstance(caught.exception,auto.MLCommercialUnavailable)

    def test_declared_other_product_is_semantic_identity_error(self):
        with self.assertRaises(auto.MLProductInconsistent):auto.extract_product(structured(url=OTHER),DIRECT)


class SocialStockTests(StockFixture):
    def test_unavailable_card(self):
        page,_,_,_=self.fake_browser(card_text='Produto indisponível R$ 100,00')
        with self.assertRaises(auto.MLCommercialUnavailable):auto.fetch_browser(SOCIAL,confirm_stock=True)
        page.goto.assert_called_once()

    def test_sold_out_card(self):
        for text in ('Estoque esgotado','Sem estoque','Produto pausado'):
            page,_,_,_=self.fake_browser(card_text=text)
            with self.subTest(text=text),self.assertRaises(auto.MLCommercialUnavailable):auto.fetch_browser(SOCIAL,confirm_stock=True)
            page.goto.assert_called_once()

    def test_card_alone_has_no_positive_stock_proof(self):
        page,_,_,_=self.fake_browser()
        card=auto.browser_social_featured(page,SOCIAL)
        self.assertFalse(auto.confirmed_stock(card,DIRECT))
        self.assertNotIn('stock_confirmed',card)

    def test_same_official_product_in_stock_confirms_card(self):
        page,browser,_,_=self.fake_browser()
        result=auto.fetch_browser(SOCIAL,confirm_stock=True)
        self.assertTrue(auto.confirmed_stock(result,DIRECT))
        self.assertEqual(result['price'],'100,00')
        self.assertEqual([c.args[0] for c in page.goto.call_args_list],[SOCIAL,DIRECT])
        self.assertTrue(all(c.kwargs['timeout']==20000 for c in page.goto.call_args_list))
        browser.close.assert_called_once()

    def test_confirmation_out_of_stock(self):
        self.fake_browser(text=structured('OutOfStock'))
        with self.assertRaises(auto.MLCommercialUnavailable):auto.fetch_browser(SOCIAL,confirm_stock=True)

    def test_confirmation_unknown(self):
        self.fake_browser(text=structured(None))
        with self.assertRaises(AffiliateError) as caught:auto.fetch_browser(SOCIAL,confirm_stock=True)
        self.assertNotIsInstance(caught.exception,auto.MLCommercialUnavailable)

    def test_confirmation_http_failures(self):
        for status in (403,404,429,500):
            page,_,_,_=self.fake_browser(status=status)
            with self.subTest(status=status),self.assertRaises(AffiliateError) as caught:auto.fetch_browser(SOCIAL,confirm_stock=True)
            self.assertNotIsInstance(caught.exception,auto.MLCommercialUnavailable)
            self.assertEqual(page.goto.call_count,2)

    def test_confirmation_missing_schema(self):
        self.fake_browser(text='pagina sem schema')
        with self.assertRaises(AffiliateError):auto.fetch_browser(SOCIAL,confirm_stock=True)

    def test_confirmation_other_product(self):
        self.fake_browser(redirected=OTHER,text=structured(url=OTHER))
        with self.assertRaises(auto.MLProductInconsistent):auto.fetch_browser(SOCIAL,confirm_stock=True)

    def test_confirmation_other_variant(self):
        self.fake_browser(target=DIRECT+'?variation_id=1',redirected=DIRECT+'?variation_id=2')
        with self.assertRaises(auto.MLProductInconsistent):auto.fetch_browser(SOCIAL,confirm_stock=True)

    def test_confirmation_cycle_not_recursive(self):
        page,_,_,_=self.fake_browser(redirected=SOCIAL)
        with self.assertRaisesRegex(AffiliateError,'ciclo'):auto.fetch_browser(SOCIAL,confirm_stock=True)
        self.assertEqual(page.goto.call_count,2)

    def test_missing_and_multiple_cta_are_read_failure(self):
        for count in (0,2):
            page,_,controls,_=self.fake_browser();controls.count.return_value=count
            with self.assertRaises(AffiliateError):auto.fetch_browser(SOCIAL,confirm_stock=True)
            page.goto.assert_called_once()

    def test_playwright_failure_is_read_failure(self):
        page,_,_,_=self.fake_browser();page.goto.side_effect=TimeoutError('browser timeout')
        with self.assertRaises(AffiliateError) as caught:auto.fetch_browser(SOCIAL,confirm_stock=True)
        self.assertNotIsInstance(caught.exception,auto.MLCommercialUnavailable)

    def test_manual_read_keeps_card_policy_without_stock_confirmation(self):
        page,_,_,_=self.fake_browser(text='no schema')
        result=auto.fetch_browser(SOCIAL)
        page.goto.assert_called_once()
        self.assertEqual(result['price'],'100,00')
        self.assertNotIn('stock_confirmed',result)


class GateStockTests(StockFixture):
    def test_social_confirmation_price_increased_is_not_hidden_by_card(self):
        self.fake_browser(text=structured(price='150.00'))
        with patch.object(auto,'fetch_http',side_effect=AffiliateError('HTTP 403')):
            self.gate_reject('PRECO_AUMENTOU')

    def test_negative_evidence_for_other_product_is_inconsistent(self):
        self.gate.ml_reader=Mock()
        self.gate.ml_reader.read.side_effect=auto.MLCommercialUnavailable('OutOfStock',resolved_url=OTHER)
        self.gate_reject('PRODUTO_INCONSISTENTE')

    def test_price_and_stock_are_independent(self):
        for price,reason in ((None,'PRECO_NAO_CONFIRMADO'),('150,00','PRECO_AUMENTOU')):
            read=auto.extract_product(structured(),DIRECT);read['price']=price
            self.gate.ml_reader=Mock();self.gate.ml_reader.read.return_value=read
            self.gate_reject(reason)
        self.gate.ml_reader.read.return_value=auto.extract_product(structured(),DIRECT)
        self.assertEqual(self.gate.validate(self.original,dict(self.original),'@audit')['prepublication_validation'],'VALIDACAO_FORTE')

    def test_stock_unknown_cannot_inherit_capture_boolean(self):
        read=auto.extract_product(structured(),DIRECT)
        for field in auto.STOCK_FIELDS:read.pop(field)
        self.original['stock_confirmed']=True
        self.gate.ml_reader=Mock();self.gate.ml_reader.read.return_value=read
        error=self.gate_reject('VALIDACAO_INDISPONIVEL');self.assertFalse(error.discard)

    def test_negative_stock_discard_only_current_revision(self):
        self.gate.ml_reader=Mock();self.gate.ml_reader.read.side_effect=auto.MLCommercialUnavailable('OutOfStock')
        error=self.gate_reject('SEM_ESTOQUE_COMPROVADO');self.assertTrue(error.discard)
        self.assertEqual(error.retry_after,0)

    def test_stock_of_other_product_not_accepted(self):
        read=auto.extract_product(structured(),DIRECT);read.update(stock_fields(OTHER))
        self.gate.ml_reader=Mock();self.gate.ml_reader.read.return_value=read
        self.gate_reject('PRODUTO_INCONSISTENTE')

    def test_native_range_still_rejected_with_valid_stock(self):
        read=auto.extract_product(structured(),DIRECT);read['price_from']=True
        self.gate.ml_reader=Mock();self.gate.ml_reader.read.return_value=read
        self.gate_reject('PRECO_VARIANTE_AMBIGUO')

    def test_fallback_read_failure_can_confirm_same_product(self):
        self.fake_browser()
        with patch.object(auto,'fetch_http',side_effect=AffiliateError('HTTP 403')):
            approved=self.gate.validate(self.original,dict(self.original),'@audit')
        self.assertTrue(approved['stock_confirmed']);self.assertEqual(approved['stock_product_id'],self.original['product_id'])

    def test_mirror_template_preserved(self):
        self.original.update(publish_mode='mirror',mirror_template='Texto original R$ 100 com condições')
        read=auto.extract_product(structured(),DIRECT)
        with patch.object(auto,'fetch_http',return_value=read):approved=self.gate.validate(self.original,dict(self.original),'@audit')
        self.assertEqual(approved['mirror_template'],self.original['mirror_template'])

    def test_cached_stock_positive_and_expiry(self):
        moment=[100]
        with patch.object(auto.time,'monotonic',side_effect=lambda:moment[0]), \
             patch.object(auto,'fetch_http',side_effect=[auto.extract_product(structured(),DIRECT),auto.MLCommercialUnavailable('OutOfStock')]) as http, \
             patch.object(auto,'fetch_browser') as browser:
            self.gate.validate(self.original,dict(self.original),'@audit')
            moment[0]=159;self.gate.validate(self.original,dict(self.original),'@audit')
            self.assertEqual(http.call_count,1)
            moment[0]=161;self.gate_reject('SEM_ESTOQUE_COMPROVADO')
            self.assertEqual(http.call_count,2);browser.assert_not_called()

    def test_negative_cache_does_not_fallback_or_requery_within_ttl(self):
        with patch.object(auto,'fetch_http',side_effect=auto.MLCommercialUnavailable('OutOfStock')) as http,patch.object(auto,'fetch_browser') as browser:
            for _ in range(2):self.gate_reject('SEM_ESTOQUE_COMPROVADO')
            http.assert_called_once();browser.assert_not_called()

    def test_native_revision_has_new_stock_read(self):
        with patch.object(auto,'fetch_http',return_value=auto.extract_product(structured(),DIRECT)) as http:
            self.original['source_revision_at']='2026-10-04T01:00:00+00:00';self.gate.validate(self.original,dict(self.original),'@audit')
            self.original['source_revision_at']='2026-10-04T01:01:00+00:00';self.gate.validate(self.original,dict(self.original),'@audit')
        self.assertEqual(http.call_count,2)

    def test_no_stock_sharing_between_products(self):
        first=offer();second=dict(first,product_id='MercadoLivre:999999',url=OTHER,original_url=OTHER)
        self.assertNotEqual(auto.AutoReader.fingerprint(first),auto.AutoReader.fingerprint(second))

    def test_async_negative_cache_preserves_semantics(self):
        pending=Future();pending.set_exception(auto.MLCommercialUnavailable('OutOfStock'))
        self.reader.jobs[auto.AutoReader.fingerprint(self.original)]=pending
        with patch.object(auto,'fetch_http') as http,patch.object(auto,'fetch_browser') as browser,self.assertRaises(auto.MLCommercialUnavailable):
            self.reader.read(self.original)
        http.assert_not_called();browser.assert_not_called()


class PublisherStockTests(StockFixture):
    def run_real(self, mode, pending=False, new_revision=False):
        ledger=Ledger(self.root/'publicacoes.sqlite3');self.stack.callback(ledger.db.close)
        original=dict(self.original)
        if pending:original.update(kind='ml_offer_pending',product_id=manual.pending_key(SOCIAL),url=SOCIAL,name='',price=None,api_image=None)
        queue=CapturedOfferQueue(self.root/'publicacoes.sqlite3');queue.replace_capture([original]);queue.close()
        self.fake_browser(text=structured() if mode!='unknown' else 'missing schema',
                          redirected=OTHER if mode=='different' else None)
        calls=[]
        def http(url):
            self.assertFalse(ledger.db.in_transaction)
            self.assertEqual(ledger.db.execute('SELECT count(*) FROM posts').fetchone()[0],0)
            calls.append(url)
            if mode=='negative':return auto.extract_product(structured('OutOfStock'),DIRECT)
            raise AffiliateError('HTTP 403')
        self.stack.enter_context(patch.object(auto,'fetch_http',side_effect=http))
        if pending:
            try:self.reader.read(original,blocking=True)
            except auto.MLCommercialUnavailable:pass
        affiliate=Mock(cookie='fake',csrf='fake',tag='fake')
        affiliate.prepare.side_effect=lambda o:dict(o,affiliate_generated=True,affiliate_url='https://meli.la/audit')
        self.stack.enter_context(patch.object(auto,'AutoReader',return_value=self.reader))
        self.stack.enter_context(patch.object(publisher,'BASE',self.root))
        self.stack.enter_context(patch.object(publisher,'Ledger',return_value=ledger))
        self.stack.enter_context(patch.object(publisher.MercadoLivreAffiliate,'from_env',return_value=affiliate))
        for factory in (publisher.ShopeeAffiliate,publisher.KabumAffiliate,publisher.AmazonCreators):
            self.stack.enter_context(patch.object(factory,'from_env',side_effect=AffiliateError('disabled locally')))
        browser=self.stack.enter_context(patch.object(auto,'fetch_browser',wraps=auto.fetch_browser))
        reserve=self.stack.enter_context(patch.object(ledger,'reserve',wraps=ledger.reserve))
        authorize=self.stack.enter_context(patch.object(ledger,'mark_sending',wraps=ledger.mark_sending))
        sender=self.stack.enter_context(patch.object(publisher,'send',return_value=(77,None)))
        shadow=self.stack.enter_context(patch.object(publisher.ShadowDistribution,'mirror_success',return_value={}))
        self.stack.enter_context(patch.object(publisher.time,'sleep',side_effect=KeyboardInterrupt))
        self.stack.enter_context(patch.dict('os.environ',{'TELEGRAM_TOKEN':'fake','TELEGRAM_CANAL':'@audit'}))
        self.stack.enter_context(patch('builtins.print'))
        if new_revision:
            method=PrePublicationGate.validate
            def validate(gate,*args,**kwargs):
                try:return method(gate,*args,**kwargs)
                except GateReject:
                    replacement=dict(original,source_revision_at=(datetime.now(timezone.utc)+timedelta(seconds=2)).isoformat(),price='90,00')
                    q=CapturedOfferQueue(self.root/'publicacoes.sqlite3')
                    try:q.replace_capture([replacement])
                    finally:q.close()
                    raise
            self.stack.enter_context(patch.object(PrePublicationGate,'validate',validate))
        with self.assertRaises(KeyboardInterrupt):publisher.run_publisher(SimpleNamespace(simular=False),Mock())
        self.network.assert_not_called()
        self.assertEqual(ledger.db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        return ledger,reserve,authorize,sender,shadow,browser

    def assert_no_send(self,result):
        ledger,*calls=result
        for call in calls[:4]:call.assert_not_called()
        self.assertEqual(ledger.db.execute('SELECT count(*) FROM posts').fetchone()[0],0)
        self.assertEqual(ledger.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)

    def test_original_negative_before_gate_no_reserve_or_browser(self):
        result=self.run_real('negative');self.assert_no_send(result);result[-1].assert_not_called()
        self.assertEqual(result[0].db.execute('SELECT reason FROM prepublication_gate').fetchall(),[('SEM_ESTOQUE_COMPROVADO',)])

    def test_social_without_confirmation_no_reserve(self):self.assert_no_send(self.run_real('unknown'))
    def test_official_identity_divergence_no_reserve(self):self.assert_no_send(self.run_real('different'))

    def test_social_same_product_confirmed_can_send(self):
        ledger,reserve,authorize,sender,shadow,browser=self.run_real('positive')
        for call in (reserve,authorize,sender,shadow,browser):call.assert_called_once()
        self.assertEqual(ledger.db.execute('SELECT status,message_id FROM posts').fetchone(),('sent',77))
        self.assertEqual(ledger.db.execute('SELECT state,external_id FROM deliveries').fetchone(),('SENT','77'))
        self.assertTrue(sender.call_args.args[2]['stock_confirmed'])

    def test_pending_negative_discard_not_transient_retry(self):
        result=self.run_real('negative',pending=True);self.assert_no_send(result);result[-1].assert_not_called()
        self.assertEqual(result[0].db.execute('SELECT count(*) FROM captured_queue').fetchone()[0],0)
        self.assertEqual(result[0].db.execute('SELECT count(*) FROM ml_resolution_failures').fetchone()[0],0)

    def test_negative_gate_preserves_new_capture_revision(self):
        result=self.run_real('negative',new_revision=True);self.assert_no_send(result)
        self.assertEqual(json.loads(result[0].db.execute('SELECT payload FROM captured_queue').fetchone()[0])['price'],'90,00')
