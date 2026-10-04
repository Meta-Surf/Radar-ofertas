"""Associação Amazon local, API/sender simulados e SQLite temporário."""
import copy
import json
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bot_ofertas_revisao as publisher
from amazon_afiliados import AmazonCreators, API_ENDPOINT, TOKEN_ENDPOINT, valid_affiliate_url
from fila_ofertas_sqlite import CapturedOfferQueue
from ofertas_core import Ledger, product
from prepublicacao import PrePublicationGate, GateReject
from shopee_afiliados import AffiliateError
from tests.test_amazon_afiliados import ASIN, TAG, DETAIL, item, response

OTHER = 'B099999999'
OTHER_LINK = f'https://www.amazon.com.br/dp/{OTHER}?tag={TAG}'


class AmazonIdentityFixture(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.path = self.root / 'publicacoes.sqlite3'
        self.ledger = Ledger(self.path); self.stack.callback(self.ledger.db.close)
        self.native = item(price=100)
        self.transport = Mock(); self.transport.post.side_effect = self.api_response
        self.client = AmazonCreators(TAG, 'fake-id', 'fake-secret', transport=self.transport)
        self.gate = PrePublicationGate(self.root, self.ledger.db, amazon_affiliate=self.client)
        self.original = dict(product_id='Amazon:'+ASIN, kind='product_offer', source='telegram', store='Amazon',
            chat_id=-1, message_id=1, url='https://www.amazon.com.br/dp/'+ASIN, name='Produto A', price='100,00',
            api_image='https://m.media-amazon.com/images/I/test.jpg',
            source_date=datetime.now(timezone.utc).isoformat(), source_revision_at=datetime.now(timezone.utc).isoformat())
        self.network = self.stack.enter_context(patch('requests.sessions.Session.request', side_effect=AssertionError('real network forbidden')))

    def api_response(self, url, **kwargs):
        self.assertFalse(self.ledger.db.in_transaction, 'API under ledger transaction')
        if url == TOKEN_ENDPOINT:
            return response(200, {'access_token':'fake', 'expires_in':3600})
        self.assertEqual(url, API_ENDPOINT)
        return response(200, {'itemsResult':{'items':[copy.deepcopy(self.native)]}})

    def prepared(self, **changes):
        return dict(self.original, affiliate_url=DETAIL, affiliate_generated=True, stock_confirmed=True, **changes)

    def reject(self, prepared, reason):
        with self.assertRaises(GateReject) as caught:
            self.gate.validate(self.original, prepared, '@audit')
        self.assertEqual(caught.exception.reason, reason)
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM posts').fetchone()[0], 0)

    def enqueue(self, value=None):
        queue = CapturedOfferQueue(self.path)
        queue.replace_capture([self.original if value is None else value]); queue.close()

    def run_real(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(publisher, 'BASE', self.root))
            stack.enter_context(patch.object(publisher, 'Ledger', return_value=self.ledger))
            stack.enter_context(patch.object(publisher.AmazonCreators, 'from_env', return_value=self.client))
            for factory in (publisher.ShopeeAffiliate, publisher.MercadoLivreAffiliate, publisher.KabumAffiliate):
                stack.enter_context(patch.object(factory, 'from_env', side_effect=AffiliateError('disabled fixture')))
            reserve = stack.enter_context(patch.object(self.ledger, 'reserve', wraps=self.ledger.reserve))
            authorize = stack.enter_context(patch.object(self.ledger, 'mark_sending', wraps=self.ledger.mark_sending))
            sender = stack.enter_context(patch.object(publisher, 'send', return_value=(77, 0)))
            shadow = stack.enter_context(patch.object(publisher.ShadowDistribution, 'mirror_success', return_value={}))
            stack.enter_context(patch.dict('os.environ', {'TELEGRAM_TOKEN':'fake', 'TELEGRAM_CANAL':'@audit', 'RADAR_DESTINOS_SHADOW':''}))
            stack.enter_context(patch.object(publisher.time, 'sleep', side_effect=KeyboardInterrupt))
            stack.enter_context(patch('builtins.print'))
            with self.assertRaises(KeyboardInterrupt):
                publisher.run_publisher(SimpleNamespace(simular=False), Mock())
        self.network.assert_not_called()
        return reserve, authorize, sender, shadow

    def no_send(self):
        for call in self.run_real(): call.assert_not_called()
        for table in ('posts', 'deliveries', 'price_history'):
            self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM '+table).fetchone()[0], 0)


class AmazonParserIdentityTests(AmazonIdentityFixture):
    def test_official_dp_and_gp_product_bind_same_asin(self):
        for path in ('/dp/', '/gp/product/'):
            with self.subTest(path=path):
                self.native['detailPageURL'] = 'https://www.amazon.com.br'+path+ASIN+'?tag='+TAG+'&ref_=audit&linkCode=ogi'
                parsed = self.client.request_item(ASIN)
                self.assertEqual(parsed['asin'], ASIN)
                self.assertEqual(product(parsed['affiliate_url'])[0], 'Amazon:'+ASIN)
                self.assertTrue(parsed['stock_confirmed'])
                self.assertEqual(parsed['price'], '100,00')
        self.assertEqual(self.transport.post.call_count, 3)  # token once, item twice

    def test_lowercase_request_item_and_detail_normalize_uppercase(self):
        self.native.update(asin=ASIN.lower(), detailPageURL=DETAIL.replace(ASIN, ASIN.lower()))
        parsed = self.client.request_item(ASIN.lower())
        self.assertEqual(parsed['asin'], ASIN)
        self.assertEqual(product(parsed['affiliate_url'])[0], 'Amazon:'+ASIN)
        self.assertEqual(self.transport.post.call_args.kwargs['json']['itemIds'], [ASIN])

    def test_valid_form_and_tag_are_not_identity_proof(self):
        self.assertTrue(valid_affiliate_url(OTHER_LINK, TAG))
        self.native['detailPageURL'] = OTHER_LINK
        with self.assertRaisesRegex(AffiliateError, 'destino afiliado diverge'):
            self.client.request_item(ASIN)

    def test_link_identity_and_form_fail_before_commercial_fields(self):
        bad = (OTHER_LINK, OTHER_LINK.replace(TAG, 'outro-20'), '', 'https://www.amazon.com.br/dp/',
            'https://www.amazon.com.br/store?tag='+TAG, 'https://external.invalid/dp/'+ASIN+'?tag='+TAG,
            'http://www.amazon.com.br/dp/'+ASIN+'?tag='+TAG, 'https://[invalid',
            DETAIL.replace('?tag='+TAG, '?tag=outro-20'), DETAIL.replace('?tag='+TAG, '?x='+TAG),
            DETAIL.replace('https://', 'https://user:password@'), DETAIL.replace('.br/', '.br:444/'))
        for link in bad:
            data = dict(self.native, detailPageURL=link)
            data.pop('itemInfo'); data.pop('offersV2'); data.pop('images')
            with self.subTest(link=link), self.assertRaises(AffiliateError) as caught:
                self.client._parse_item(data, ASIN)
            self.assertIn('afiliado', str(caught.exception))

    def test_direct_parser_requires_requested_item_asin(self):
        for value in (OTHER, None, 'invalid'):
            with self.subTest(asin=value), self.assertRaisesRegex(AffiliateError, 'ASIN do item'):
                self.client._parse_item(dict(self.native, asin=value), ASIN)

    def test_request_item_does_not_use_other_api_item(self):
        self.native['asin'] = OTHER
        with self.assertRaisesRegex(AffiliateError, 'ASIN não encontrado'):
            self.client.request_item(ASIN)

    def test_invalid_requested_asin_has_no_transport(self):
        for value in ('invalid', ASIN+'0', '', None):
            with self.subTest(asin=value), self.assertRaises(AffiliateError): self.client.request_item(value)
        self.transport.post.assert_not_called()

    def test_prepare_refuses_canonical_identity_different_from_capture(self):
        with self.assertRaisesRegex(AffiliateError, 'divergente da URL'):
            self.client.prepare(dict(self.original, url='https://www.amazon.com.br/dp/'+OTHER))
        self.transport.post.assert_not_called()

    def test_no_product_result_cache_shared_between_asins(self):
        first = self.client.request_item(ASIN)
        self.native.update(asin=OTHER, detailPageURL=OTHER_LINK)
        second = self.client.request_item(OTHER)
        self.assertEqual(product(first['affiliate_url'])[0], 'Amazon:'+ASIN)
        self.assertEqual(product(second['affiliate_url'])[0], 'Amazon:'+OTHER)
        self.assertEqual(self.transport.post.call_count, 3)

    def test_correct_identity_does_not_compensate_invalid_commercial_data(self):
        mutations = [lambda x:x['offersV2']['listings'][0]['availability'].update(type='OUT_OF_STOCK'),
            lambda x:x['offersV2']['listings'][0]['availability'].clear(),
            lambda x:x['offersV2']['listings'][0]['condition'].update(value='Used'),
            lambda x:x['offersV2']['listings'][0]['price']['money'].update(currency='USD'),
            lambda x:x['offersV2']['listings'][0]['price']['money'].update(amount=None),
            lambda x:x['offersV2']['listings'][0]['price']['money'].update(amount=0),
            lambda x:x['offersV2']['listings'][0]['price']['money'].update(amount=-1),
            lambda x:x['offersV2']['listings'][0]['price']['money'].update(amount='NaN'),
            lambda x:x['images']['primary'].clear(),
            lambda x:x['images']['primary']['medium'].update(url='https://external.invalid/image.jpg')]
        for index, mutate in enumerate(mutations):
            data = copy.deepcopy(self.native); mutate(data)
            with self.subTest(case=index), self.assertRaises(AffiliateError): self.client._parse_item(data, ASIN)


class AmazonGateIdentityTests(AmazonIdentityFixture):
    def test_gate_bypassed_parser_other_affiliate_asin_is_inconsistent(self):
        self.reject(dict(self.prepared(), affiliate_url=OTHER_LINK), 'PRODUTO_INCONSISTENTE')

    def test_gate_correct_asin_wrong_or_missing_tag_is_invalid_link(self):
        for link in (DETAIL.replace(TAG, 'outro-20'), DETAIL.split('?')[0]):
            with self.subTest(link=link): self.reject(dict(self.prepared(), affiliate_url=link), 'LINK_INVALIDO')

    def test_gate_partial_malformed_or_external_affiliate_is_invalid_link(self):
        for link in (None, '', 'https://www.amazon.com.br/dp/', 'https://[invalid',
                     'https://external.invalid/dp/'+ASIN+'?tag='+TAG, DETAIL.replace('.br/', '.br:444/')):
            with self.subTest(link=link): self.reject(dict(self.prepared(), affiliate_url=link), 'LINK_INVALIDO')

    def test_gate_other_link_with_wrong_tag_is_invalid_link(self):
        self.reject(dict(self.prepared(), affiliate_url=OTHER_LINK.replace(TAG, 'outro-20')), 'LINK_INVALIDO')

    def test_gate_canonical_identity_must_match_capture(self):
        self.reject(dict(self.prepared(), url='https://www.amazon.com.br/dp/'+OTHER), 'PRODUTO_INCONSISTENTE')
        self.reject(dict(self.prepared(), product_id='Amazon:'+OTHER, url='https://www.amazon.com.br/dp/'+OTHER,
                         affiliate_url=OTHER_LINK), 'PRODUTO_INCONSISTENTE')

    def test_gate_wrong_store_or_invalid_capture_cannot_authorize(self):
        self.original['store'] = 'Shopee'
        self.reject(self.prepared(store='Amazon'), 'PRODUTO_INCONSISTENTE')

    def test_gate_recognized_paths_and_query_keep_same_identity(self):
        for link in (DETAIL, DETAIL.replace('/dp/', '/gp/product/'), DETAIL.replace(ASIN, ASIN.lower())):
            approved = self.gate.validate(self.original, dict(self.prepared(), affiliate_url=link), '@audit')
            self.assertEqual(approved['prepublication_validation'], 'VALIDACAO_FORTE')

    def test_gate_correct_identity_still_requires_price_and_stock(self):
        for field, value, reason in (('price', None, 'PRECO_NAO_CONFIRMADO'),
                                    ('price', '150,00', 'PRECO_AUMENTOU'),
                                    ('stock_confirmed', False, 'SEM_ESTOQUE_COMPROVADO')):
            self.reject(dict(self.prepared(), **{field:value}), reason)


class AmazonPublisherIdentityTests(AmazonIdentityFixture):
    def test_original_inconsistent_identity_has_zero_reserve_authorize_sender_shadow_history(self):
        self.native['detailPageURL'] = OTHER_LINK; self.enqueue(); self.no_send()

    def test_bypassed_parser_is_blocked_by_real_gate_before_reserve(self):
        self.enqueue()
        with patch.object(self.client, 'prepare', return_value=dict(self.prepared(), affiliate_url=OTHER_LINK)):
            self.no_send()
        self.assertEqual(self.ledger.db.execute('SELECT reason FROM prepublication_gate').fetchone()[0], 'PRODUTO_INCONSISTENTE')

    def test_correct_identity_reaches_confirmed_content_and_history(self):
        self.enqueue(); reserve, authorize, sender, shadow = self.run_real()
        for call in (reserve, authorize, sender, shadow): self.assertEqual(call.call_count, 1)
        sent = sender.call_args.args[2]
        self.assertEqual(product(sent['affiliate_url'])[0], sent['product_id'])
        self.assertEqual(self.ledger.db.execute('SELECT status,message_id FROM posts').fetchone(), ('sent',77))
        state, external, payload = self.ledger.db.execute('SELECT state,external_id,payload FROM deliveries').fetchone()
        self.assertEqual((state,external), ('SENT','77'))
        self.assertEqual(product(json.loads(payload)['affiliate_url'])[0], 'Amazon:'+ASIN)
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM price_history').fetchone()[0], 1)

    def test_new_revision_during_old_parser_failure_survives_and_can_publish(self):
        self.native['detailPageURL'] = OTHER_LINK; self.enqueue()
        newer = dict(self.original, price='90,00', source_revision_at=(datetime.now(timezone.utc)+timedelta(seconds=2)).isoformat())
        parse = self.client._parse_item
        def update_then_parse(*args): self.enqueue(newer); return parse(*args)
        with patch.object(self.client, '_parse_item', side_effect=update_then_parse): self.no_send()
        queue = CapturedOfferQueue(self.path)
        self.assertEqual(queue.pending()[0]['price'], '90,00'); queue.close()
        self.native = item(price=90)
        reserve, authorize, sender, shadow = self.run_real()
        for call in (reserve, authorize, sender, shadow): self.assertEqual(call.call_count, 1)
        self.assertEqual(sender.call_args.args[2]['price'], '90,00')

    def test_new_revision_during_old_gate_refusal_is_preserved(self):
        self.enqueue()
        newer = dict(self.original, price='90,00', source_revision_at=(datetime.now(timezone.utc)+timedelta(seconds=2)).isoformat())
        def prepare_then_update(value): self.enqueue(newer); return dict(self.prepared(), affiliate_url=OTHER_LINK)
        with patch.object(self.client, 'prepare', side_effect=prepare_then_update): self.no_send()
        queue = CapturedOfferQueue(self.path)
        self.assertEqual(queue.pending()[0]['price'], '90,00'); queue.close()
        self.native = item(price=90)
        self.assertEqual(self.run_real()[2].call_count, 1)


if __name__ == '__main__': unittest.main()
