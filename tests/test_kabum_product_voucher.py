"""Voucher de produto não é alerta genérico. Relógio aware e zero rede real."""
import json
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
import bot_ofertas_revisao as publisher
import radar_kabum as kabum
from awin_kabum import AwinKabumAPI, product_voucher_metadata
from inteligencia_ofertas import Intelligence
from kabum_afiliados import KabumAffiliate
from ofertas_core import Ledger, caption
from prepublicacao import PrePublicationGate, GateReject
from tests.test_radar_publication_selection import feed

class VoucherFixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.path=self.root/'publicacoes.sqlite3'
        self.moment=datetime(2026,10,3,13,0,tzinfo=timezone.utc)
        fixture=self
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):
                return fixture.moment.astimezone(tz) if tz else fixture.moment.replace(tzinfo=None)
        self.clock=Clock
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for module in ('prepublicacao','radar_kabum','bot_ofertas_revisao','ofertas_core'):
            self.stack.enter_context(patch(module+'.datetime',Clock))
        self.stack.enter_context(patch('time.time',side_effect=lambda:self.moment.timestamp()))
        self.catalog=kabum.init_db(self.root/'kabum_historico.sqlite3');self.addCleanup(self.catalog.close)
        self.moment-=timedelta(hours=1);kabum.ingest(feed({'21':'100.00'}),self.catalog)
        self.moment+=timedelta(hours=1);kabum.ingest(feed({'21':'80.00'}),self.catalog)
        self.native=self.native_voucher()
        self.api=Mock(enabled=True);self.api.offers.return_value=[self.native]
        self.stack.enter_context(patch('awin_kabum.AwinKabumAPI.from_env',return_value=self.api))
        self.get=self.stack.enter_context(patch('requests.get',side_effect=AssertionError('real network forbidden')))
        self.post=self.stack.enter_context(patch('requests.post',side_effect=AssertionError('real network forbidden')))
        self.ledger=Ledger(self.path);self.addCleanup(self.ledger.db.close)
        self.i=Intelligence(self.ledger.db)
        self.affiliate=KabumAffiliate(self.root/'kabum_historico.sqlite3',publisher_id='3106767')
        self.gate=PrePublicationGate(self.root,self.ledger.db,kabum_affiliate=self.affiliate)

    def native_voucher(self, **changes):
        item={'promotionId':777,'type':'voucher','advertiser':{'id':17729,'joined':True},
            'url':'https://www.kabum.com.br/produto/21',
            'urlTracking':'https://www.awin1.com/pclick.php?p=21&a=3106767&m=17729',
            'title':'10% OFF no produto com TESTE10','description':'10% OFF','terms':'Selecionados',
            'startDate':(self.moment-timedelta(hours=1)).isoformat(),
            'endDate':(self.moment+timedelta(minutes=1)).isoformat(), 'voucher':{'code':'TESTE10'}}
        item.update(changes);return item

    def candidates(self, items=None):
        mapping=AwinKabumAPI().product_offer_map([self.native] if items is None else items)
        return kabum.production_candidates(self.catalog,official_offers=mapping,now=self.moment.timestamp())

    def candidate(self):return self.candidates()[0]

    def validate(self,offer=None):
        offer=self.candidate() if offer is None else offer
        return self.gate.validate(offer,self.affiliate.prepare(offer),'@audit')

    def reject(self,reason,offer):
        with self.assertRaises(GateReject) as caught:self.validate(offer)
        self.assertEqual(caught.exception.reason,reason);return caught.exception

    def selected(self,offer):
        self.i.enqueue([offer])
        return self.i.pending('@audit',include_selection=True)[0]['_queue_selection']

    def run_publisher(self, reserve_hook=None):
        with ExitStack() as stack:
            stack.enter_context(patch.object(publisher,'BASE',self.root))
            stack.enter_context(patch.object(publisher,'Ledger',return_value=self.ledger))
            stack.enter_context(patch.object(publisher.KabumAffiliate,'from_env',return_value=self.affiliate))
            for factory in (publisher.ShopeeAffiliate,publisher.MercadoLivreAffiliate,publisher.AmazonCreators):
                stack.enter_context(patch.object(factory,'from_env',side_effect=publisher.AffiliateError('disabled fixture')))
            reserve=self.ledger.reserve
            def wrapped(*args,**kwargs):
                day=reserve(*args,**kwargs)
                if reserve_hook:reserve_hook()
                return day
            reserve_spy=stack.enter_context(patch.object(self.ledger,'reserve',side_effect=wrapped))
            authorize=stack.enter_context(patch.object(self.ledger,'mark_sending',wraps=self.ledger.mark_sending))
            sender=stack.enter_context(patch.object(publisher,'send',return_value=(77,0)))
            shadow=stack.enter_context(patch.object(publisher.ShadowDistribution,'mirror_success',return_value={}))
            stack.enter_context(patch.object(publisher.time,'sleep',side_effect=KeyboardInterrupt))
            stack.enter_context(patch.dict('os.environ',{'TELEGRAM_TOKEN':'fake','TELEGRAM_CANAL':'@audit','RADAR_DESTINOS_SHADOW':''}))
            stack.enter_context(patch('builtins.print'))
            with self.assertRaises(KeyboardInterrupt):publisher.run_publisher(SimpleNamespace(simular=False),Mock())
            self.get.assert_not_called();self.post.assert_not_called()
            return reserve_spy,authorize,sender,shadow

class ProductVoucherTests(VoucherFixture):
    def test_provenance_and_code_belong_to_one_voucher_not_first_promotion(self):
        promotion=self.native_voucher(promotionId=1,type='promotion',title='Outra promoção')
        offer=self.candidates([promotion,self.native])[0]
        v=offer['kabum_voucher'];self.assertEqual(v['promotion_id'],'777')
        self.assertEqual(v['coupon'],offer['coupon']);self.assertEqual(v['product_id'],'KaBuM:21')
        self.assertEqual(v['terms'],'Selecionados');self.assertEqual(offer['official_offer_title'],self.native['title'])

    def test_multiple_vouchers_are_not_arbitrarily_selected_in_either_order(self):
        other=self.native_voucher(promotionId=888,voucher={'code':'OUTRO10'})
        for items in ([self.native,other],[other,self.native]):self.assertEqual(self.candidates(items),[])

    def test_identical_duplicate_is_one_commercial_unit(self):
        self.assertEqual(len(self.candidates([self.native,dict(self.native)])),1)

    def test_invalid_candidate_is_not_silently_changed_into_product_without_coupon(self):
        for changes in ({'endDate':None},{'startDate':'invalid'},{'voucher':{'code':''}}):
            self.assertEqual(self.candidates([self.native_voucher(**changes)]),[])

    def test_native_digest_is_stable_without_now_and_changes_with_commercial_fields(self):
        first=self.candidate()['kabum_voucher'];self.moment+=timedelta(seconds=1)
        self.assertEqual(first,self.candidate()['kabum_voucher'])
        for changes in ({'promotionId':778},{'voucher':{'code':'OTHER10'}},
                        {'startDate':(self.moment-timedelta(hours=2)).isoformat()},
                        {'endDate':(self.moment+timedelta(minutes=3)).isoformat()}, {'terms':'Compra mínima 100'}):
            other=self.candidates([self.native_voucher(**changes)])[0]['kabum_voucher']
            self.assertNotEqual(first['revision_digest'],other['revision_digest'])

    def test_active_voucher_passes_with_local_authorization_proof(self):
        result=self.validate();self.assertEqual(result['coupon'],'TESTE10')
        self.assertEqual(result['_voucher_proof'],result['kabum_voucher']);self.api.offers.assert_called_once()

    def test_start_is_inclusive_and_end_is_exclusive(self):
        offer=self.candidate();start=datetime.fromisoformat(offer['kabum_voucher']['start_date'])
        end=datetime.fromisoformat(offer['kabum_voucher']['end_date'])
        self.moment=start-timedelta(microseconds=1);self.reject('CUPOM_NAO_INICIADO',offer)
        self.moment=start;self.validate(offer)
        self.moment=end-timedelta(microseconds=1);self.validate(offer)
        self.moment=end;self.assertTrue(self.reject('CUPOM_EXPIRADO',offer).discard)

    def test_missing_invalid_or_naive_dates_fail_closed(self):
        offer=self.candidate()
        for value in (None,'bad','2026-10-03T13:01:00','2026-10-03T13:01:00+00:99','2026-10-03T13:01:00+25:00'):
            bad=dict(offer,kabum_voucher=dict(offer['kabum_voucher'],end_date=value))
            error=self.reject('CUPOM_VALIDADE_NAO_CONFIRMADA',bad)
            self.assertFalse(error.discard);self.assertGreater(error.retry_after,0)

    def test_offsets_are_normalized_without_host_timezone(self):
        other=self.native_voucher(startDate='2026-10-03T09:00:00-03:00',endDate='2026-10-03T10:01:00-03:00')
        self.api.offers.return_value=[other]
        self.assertEqual(self.validate(self.candidates([other])[0])['kabum_voucher']['end_date'],'2026-10-03T13:01:00+00:00')

    def test_product_without_coupon_does_not_call_offers_api(self):
        offer=self.candidates([])[0];self.assertNotIn('kabum_voucher',offer)
        self.assertEqual(self.validate(offer)['prepublication_validation'],'VALIDACAO_FORTE')
        self.api.offers.assert_not_called()

    def test_legacy_textual_code_without_provenance_is_retry_not_permission(self):
        offer=self.candidate();offer.pop('kabum_voucher')
        self.assertFalse(self.reject('CUPOM_VALIDADE_NAO_CONFIRMADA',offer).discard)

    def test_product_promotion_code_type_and_advertiser_must_match(self):
        offer=self.candidate()
        for changes in ({'url':'https://www.kabum.com.br/produto/22'}, {'promotionId':888},
                        {'voucher':{'code':'OTHER10'}},{'type':'promotion'},
                        {'advertiser':{'id':99,'joined':True}}):
            self.gate._runtime_cache.clear();self.api.offers.return_value=[dict(self.native,**changes)]
            self.assertFalse(self.reject('CUPOM_VALIDADE_NAO_CONFIRMADA',offer).discard)

    def test_duplicate_promotion_identity_is_ambiguous_at_revalidation(self):
        self.api.offers.return_value=[self.native,dict(self.native,url='https://www.kabum.com.br/produto/22')]
        self.reject('CUPOM_VALIDADE_NAO_CONFIRMADA',self.candidate())

    def test_other_promotion_does_not_replace_the_explicit_selected_identity(self):
        self.api.offers.return_value=[self.native_voucher(promotionId=888),self.native]
        self.assertEqual(self.validate(self.candidate())['_voucher_proof']['promotion_id'],'777')

    def test_empty_partial_or_removed_promotion_is_conservative_retry(self):
        offer=self.candidate()
        for items in ([],[{'promotionId':777}], [dict(self.native,endDate=None)], [dict(self.native,startDate=None)]):
            self.gate._runtime_cache.clear();self.api.offers.return_value=items
            self.assertFalse(self.reject('CUPOM_VALIDADE_NAO_CONFIRMADA',offer).discard)

    def test_api_failures_are_unavailable_never_validated(self):
        offer=self.candidate()
        errors=[requests.Timeout(),requests.ConnectionError(),requests.HTTPError('HTTP 403'),
                requests.HTTPError('HTTP 429'),requests.HTTPError('HTTP 500'),ValueError('invalid JSON')]
        for error in errors:
            self.api.offers.side_effect=error;self.gate._runtime_cache.clear()
            self.assertFalse(self.reject('VALIDACAO_INDISPONIVEL',offer).discard)

    def test_disabled_or_non_list_api_is_unavailable(self):
        offer=self.candidate();self.api.enabled=False;self.reject('VALIDACAO_INDISPONIVEL',offer)
        self.api.enabled=True;self.api.offers.return_value={'partial':True}
        self.reject('VALIDACAO_INDISPONIVEL',offer)

    def test_malformed_native_units_never_become_permission_or_product_without_coupon(self):
        offer=self.candidate()
        for malformed in ('broken', ['partial']):
            native=dict(self.native,voucher=malformed)
            self.assertEqual(self.candidates([native]),[])
            self.gate._runtime_cache.clear();self.api.offers.return_value=[native]
            self.assertFalse(self.reject('CUPOM_VALIDADE_NAO_CONFIRMADA',offer).discard)
            self.gate._runtime_cache.clear();self.api.offers.return_value=[dict(self.native,advertiser=malformed)]
            self.assertFalse(self.reject('CUPOM_VALIDADE_NAO_CONFIRMADA',offer).discard)

    def test_cached_list_never_extends_end_date(self):
        offer=self.candidate();self.moment+=timedelta(seconds=30);self.validate(offer)
        self.moment+=timedelta(seconds=31);self.reject('CUPOM_EXPIRADO',offer)
        self.api.offers.assert_called_once();self.assertIn('awin-kabum-offers',self.gate._runtime_cache)

    def test_cache_expiration_reloads_removed_promotion(self):
        self.native['endDate']=(self.moment+timedelta(minutes=10)).isoformat()
        offer=self.candidate()
        with patch('prepublicacao.time.monotonic',return_value=0):self.validate(offer)
        self.api.offers.return_value=[]
        with patch('prepublicacao.time.monotonic',return_value=61):self.reject('CUPOM_VALIDADE_NAO_CONFIRMADA',offer)
        self.assertEqual(self.api.offers.call_count,2)

    def test_updated_future_revision_after_expired_can_be_revalidated(self):
        old=self.candidate();self.moment+=timedelta(minutes=2);self.reject('CUPOM_EXPIRADO',old)
        self.native=self.native_voucher();self.api.offers.return_value=[self.native]
        new=self.candidate();self.assertNotEqual(old['kabum_voucher']['revision_digest'],new['kabum_voucher']['revision_digest'])
        self.validate(new)

    def test_period_update_requires_the_matching_new_revision(self):
        old=self.candidate();self.api.offers.return_value=[dict(self.native,endDate='2026-10-03T13:05:00+00:00')]
        self.reject('CUPOM_VALIDADE_NAO_CONFIRMADA',old)

    def test_original_real_publisher_reproduction_expired_in_queue_sends_nothing(self):
        offer=self.candidate();self.selected(offer);self.moment+=timedelta(minutes=2)
        reserve,authorize,sender,shadow=self.run_publisher()
        for spy in (reserve,authorize,sender,shadow):spy.assert_not_called()
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM posts').fetchone()[0],0)
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)
        self.assertEqual(self.ledger.db.execute('SELECT status,reason FROM prepublication_gate').fetchall(),[('BLOQUEADA','CUPOM_EXPIRADO')])
