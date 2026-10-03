"""Período nativo de produto: publicador real, SQLite temporário, zero rede."""
import copy
import json
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bot_ofertas_revisao as publisher
from distribuicao import payload_revision
from inteligencia_ofertas import Intelligence
from ofertas_core import Ledger
from prepublicacao import PrePublicationGate, GateReject
from radar_shopee import candidate, product_offer_period, refresh, ShopeePeriodError
from revisao_publicacao import selection
from shopee_afiliados import ShopeeAffiliate, AffiliateError
from tests.test_radar_shopee import node, result


class PeriodFixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.path=self.root/'publicacoes.sqlite3'
        self.moment=datetime(2026,10,3,13,0,tzinfo=timezone.utc)
        fixture=self
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):return fixture.moment.astimezone(tz) if tz else fixture.moment.replace(tzinfo=None)
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for module in ('prepublicacao','ofertas_core','bot_ofertas_revisao','radar_shopee'):
            self.stack.enter_context(patch(module+'.datetime',Clock))
        self.stack.enter_context(patch('time.time',side_effect=lambda:self.moment.timestamp()))
        self.native=node(priceMin='100.00',priceMax='100.00',periodStartTime=int(self.moment.timestamp())-3600,
                         periodEndTime=int(self.moment.timestamp())+60)
        self.original=candidate(self.native)
        self.ledger=Ledger(self.path);self.addCleanup(self.ledger.db.close)
        self.i=Intelligence(self.ledger.db)
        self.link_hook=self.details_hook=None
        self.response_nodes=None
        self.transport=Mock();self.transport.post.side_effect=self.api_response
        self.client=ShopeeAffiliate('123','fake',transport=self.transport)
        self.gate=PrePublicationGate(self.root,self.ledger.db,shopee=self.client)
        self.get=self.stack.enter_context(patch('requests.get',side_effect=AssertionError('real network forbidden')))
        self.post=self.stack.enter_context(patch('requests.post',side_effect=AssertionError('real network forbidden')))

    def api_response(self,url,**kwargs):
        self.assertFalse(self.ledger.db.in_transaction,'API under ledger transaction')
        query=json.loads(kwargs['data'])['query']
        if 'generateShortLink' in query:
            if self.link_hook:self.link_hook()
            data={'generateShortLink':{'shortLink':'https://s.shopee.com.br/audit'}}
        else:
            if 'priceMin' not in query and self.details_hook:self.details_hook()
            data=result([self.native] if self.response_nodes is None else self.response_nodes)
        return SimpleNamespace(status_code=200,json=lambda:{'data':data})

    def selected(self,offer=None):
        offer=copy.deepcopy(self.original if offer is None else offer);self.i.enqueue([offer])
        raw=self.ledger.db.execute('SELECT payload FROM radar_queue WHERE product=?',(offer['product_id'],)).fetchone()[0]
        return selection('radar_queue',offer['product_id'],raw)

    def validate(self,prepared=None,original=None):
        original=self.original if original is None else original
        return self.gate.validate(original,self.client.prepare(original) if prepared is None else prepared,'@audit')

    def rejected(self,reason,prepared,original=None):
        with self.assertRaises(GateReject) as caught:self.validate(prepared,original)
        self.assertEqual(caught.exception.reason,reason);return caught.exception

    def run_publisher(self,reserve_hook=None):
        with ExitStack() as stack:
            stack.enter_context(patch.object(publisher,'BASE',self.root))
            stack.enter_context(patch.object(publisher,'Ledger',return_value=self.ledger))
            stack.enter_context(patch.object(publisher.ShopeeAffiliate,'from_env',return_value=self.client))
            for factory in (publisher.MercadoLivreAffiliate,publisher.KabumAffiliate,publisher.AmazonCreators):
                stack.enter_context(patch.object(factory,'from_env',side_effect=AffiliateError('disabled fixture')))
            method=self.ledger.reserve
            def wrapped(*args,**kwargs):
                day=method(*args,**kwargs)
                if day and reserve_hook:reserve_hook()
                return day
            reserved=stack.enter_context(patch.object(self.ledger,'reserve',side_effect=wrapped))
            authorized=stack.enter_context(patch.object(self.ledger,'mark_sending',wraps=self.ledger.mark_sending))
            sender=stack.enter_context(patch.object(publisher,'send',return_value=(77,0)))
            shadow=stack.enter_context(patch.object(publisher.ShadowDistribution,'mirror_success',return_value={}))
            stack.enter_context(patch.object(publisher.time,'sleep',side_effect=KeyboardInterrupt))
            stack.enter_context(patch.dict('os.environ',{'TELEGRAM_TOKEN':'fake','TELEGRAM_CANAL':'@audit','RADAR_DESTINOS_SHADOW':''}))
            stack.enter_context(patch('builtins.print'))
            with self.assertRaises(KeyboardInterrupt):publisher.run_publisher(SimpleNamespace(simular=False),Mock())
            self.get.assert_not_called();self.post.assert_not_called()
            return reserved,authorized,sender,shadow


class ProductPeriodTests(PeriodFixture):
    def test_candidate_preserves_integer_native_identity_and_period(self):
        self.assertEqual(self.original['shopee_offer_period'],dict(shop_id='123',item_id='456',
            start=self.native['periodStartTime'],end=self.native['periodEndTime']))
        period=dict(self.original['shopee_offer_period'],start=str(self.native['periodStartTime']))
        self.assertEqual(product_offer_period(period,self.original['product_id']),self.original['shopee_offer_period'])

    def test_missing_zero_negative_fractional_boolean_or_inverted_period_fails_closed(self):
        period=self.original['shopee_offer_period']
        for value in (None,0,-1,'invalid','1.5',1.5,True,'１２３'):
            for field,native in (('start','periodStartTime'),('end','periodEndTime')):
                with self.subTest(value=value,field=field):
                    self.assertIsNone(candidate(dict(self.native,**{native:value})))
                    prepared=dict(self.original,affiliate_generated=True,affiliate_url='https://s.shopee.com.br/audit',
                                  shopee_offer_period=dict(period,**{field:value}))
                    self.assertFalse(self.rejected('OFERTA_VALIDADE_NAO_CONFIRMADA',prepared).discard)
        self.assertIsNone(candidate(dict(self.native,periodEndTime=self.native['periodStartTime'])))

    def test_gate_start_inclusive_end_exclusive_and_future_retry(self):
        prepared=self.client.prepare(self.original);period=self.original['shopee_offer_period']
        self.moment=datetime.fromtimestamp(period['start']-1,timezone.utc)
        self.assertFalse(self.rejected('OFERTA_NAO_INICIADA',prepared).discard)
        self.moment=datetime.fromtimestamp(period['start'],timezone.utc);self.validate(prepared)
        self.moment=datetime.fromtimestamp(period['end'],timezone.utc)-timedelta(microseconds=1);self.validate(prepared)
        for second in (period['end'],period['end']+1):
            self.moment=datetime.fromtimestamp(second,timezone.utc)
            self.assertTrue(self.rejected('OFERTA_EXPIRADA',prepared).discard)

    def test_association_rejects_other_shop_or_item_and_incoherent_url(self):
        prepared=self.client.prepare(self.original)
        for changes in ({'shop_id':'999'},{'item_id':'999'}):
            broken=dict(prepared,shopee_offer_period=dict(prepared['shopee_offer_period'],**changes))
            self.rejected('OFERTA_VALIDADE_NAO_CONFIRMADA',broken)
        self.rejected('PRODUTO_INCONSISTENTE',dict(prepared,url='https://shopee.com.br/product/123/999'))
        self.rejected('OFERTA_VALIDADE_NAO_CONFIRMADA',prepared,dict(self.original,store=None))

    def test_refresh_uses_reconsulted_period_and_gate_refuses_changed_period(self):
        self.native=dict(self.native,periodEndTime=self.native['periodEndTime']+600)
        prepared=self.client.prepare(self.original)
        self.assertEqual(prepared['shopee_offer_period']['end'],self.native['periodEndTime'])
        self.assertFalse(self.rejected('REVISAO_COMERCIAL_ALTERADA',prepared).discard)

    def test_new_collected_revision_with_new_period_can_be_validated(self):
        self.native=dict(self.native,periodEndTime=self.native['periodEndTime']+600)
        new=candidate(self.native);self.assertEqual(self.validate(original=new)['_shopee_period_proof'],new['shopee_offer_period'])

    def test_same_period_refresh_is_strong_validation(self):
        prepared=self.validate();self.assertEqual(prepared['prepublication_validation'],'VALIDACAO_FORTE')
        self.assertEqual(prepared['_shopee_period_proof'],self.original['shopee_offer_period'])

    def test_period_proof_has_no_clock_and_each_native_field_changes_revision(self):
        first=self.original['shopee_offer_period'];self.moment+=timedelta(seconds=1)
        self.assertEqual(first,candidate(self.native)['shopee_offer_period'])
        for field,value in (('start',first['start']-1),('end',first['end']+1),('shop_id','999'),('item_id','999')):
            self.assertNotEqual(payload_revision(first),payload_revision(dict(first,**{field:value})))
        self.assertEqual(set(first),{'shop_id','item_id','start','end'})

    def test_legacy_without_persisted_period_is_retry_even_after_fresh_prepare(self):
        legacy=dict(self.original);legacy.pop('shopee_offer_period');self.selected(legacy)
        prepared=self.client.prepare(legacy)
        self.assertFalse(self.rejected('OFERTA_VALIDADE_NAO_CONFIRMADA',prepared,legacy).discard)
        _,_,sender,shadow=self.run_publisher();sender.assert_not_called();shadow.assert_not_called()
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM radar_queue').fetchone()[0],1)

    def test_price_increase_and_decrease_remain_independent_requirements(self):
        self.native=dict(self.native,priceMin='120.00',priceMax='120.00')
        self.rejected('PRECO_AUMENTOU',self.client.prepare(self.original))
        self.native=dict(self.native,priceMin='90.00',priceMax='90.00')
        self.assertEqual(self.validate()['price'],'90,00')

    def test_expired_period_precedes_price_and_invalid_price_still_blocks(self):
        prepared=self.client.prepare(self.original);self.moment+=timedelta(minutes=2)
        self.rejected('OFERTA_EXPIRADA',dict(prepared,price='120,00'))
        self.moment-=timedelta(minutes=2)
        for price in ('0,00','-1,00',None):self.rejected('PRECO_NAO_CONFIRMADO',dict(prepared,price=price))

    def test_refresh_unknown_or_partial_is_retry_without_proof(self):
        for nodes in ([],[{}],[dict(self.native,shopId=999)],[dict(self.native,itemId=999)],
                      [dict(self.native,periodEndTime=None)],[dict(self.native,periodStartTime=0)]):
            self.response_nodes=nodes
            with self.assertRaises(ShopeePeriodError):self.client.prepare(self.original)
        self.selected();_,_,sender,shadow=self.run_publisher()
        sender.assert_not_called();shadow.assert_not_called()
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM radar_queue').fetchone()[0],1)

    def test_multiple_nodes_select_exact_identity_but_conflicting_matches_are_ambiguous(self):
        self.response_nodes=[dict(self.native,itemId=999),self.native]
        self.assertEqual(refresh(self.client,self.original)['product_id'],self.original['product_id'])
        self.response_nodes=[self.native,dict(self.native,periodEndTime=self.native['periodEndTime']+1)]
        with self.assertRaises(ShopeePeriodError):refresh(self.client,self.original)
        self.response_nodes=[self.native,dict(self.native)]
        self.assertEqual(refresh(self.client,self.original)['shopee_offer_period'],self.original['shopee_offer_period'])

    def test_details_of_other_item_do_not_replace_selected_image(self):
        original_response=self.api_response
        def response(url,**kwargs):
            if 'priceMin' not in json.loads(kwargs['data'])['query'] and 'generateShortLink' not in json.loads(kwargs['data'])['query']:
                return SimpleNamespace(status_code=200,json=lambda:{'data':result([dict(self.native,itemId=999,imageUrl='https://down-br.img.susercontent.com/other')])})
            return original_response(url,**kwargs)
        self.transport.post.side_effect=response
        self.assertEqual(self.client.prepare(self.original)['api_image'],self.native['imageUrl'])

    def test_api_failures_never_create_proof_or_send(self):
        import requests
        for error in (requests.Timeout(),requests.ConnectionError()):
            self.transport.post.side_effect=error
            with self.assertRaises(AffiliateError):self.client.prepare(self.original)
        for status in (403,429,500):
            self.transport.post.side_effect=None;self.transport.post.return_value=SimpleNamespace(status_code=status)
            with self.assertRaises(AffiliateError):self.client.prepare(self.original)
        self.transport.post.return_value=Mock(status_code=200);self.transport.post.return_value.json.side_effect=ValueError('invalid JSON')
        with self.assertRaises(AffiliateError):self.client.prepare(self.original)
        self.selected();_,_,sender,shadow=self.run_publisher();sender.assert_not_called();shadow.assert_not_called()
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM radar_queue').fetchone()[0],1)

    def test_original_prepare_expiration_publication_is_permanently_blocked(self):
        self.selected();self.link_hook=lambda:setattr(self,'moment',self.moment+timedelta(minutes=2))
        reserve,authorize,sender,shadow=self.run_publisher()
        reserve.assert_not_called();authorize.assert_not_called();sender.assert_not_called();shadow.assert_not_called()
        self.assertEqual(self.ledger.db.execute('SELECT reason FROM prepublication_gate').fetchone()[0],'OFERTA_EXPIRADA')
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM posts').fetchone()[0],0)
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)

    def test_details_can_cross_end_and_gate_blocks_before_reserve(self):
        self.selected();self.details_hook=lambda:setattr(self,'moment',self.moment+timedelta(minutes=2))
        reserve,authorize,sender,shadow=self.run_publisher()
        for spy in (reserve,authorize,sender,shadow):spy.assert_not_called()
