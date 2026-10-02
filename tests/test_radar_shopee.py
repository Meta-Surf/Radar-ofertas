import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from radar_shopee import candidate, collect, refresh
from shopee_afiliados import AffiliateError, ShopeeAffiliate
from ofertas_core import Ledger, caption, product
from inteligencia_ofertas import Intelligence
from fila_ofertas_sqlite import CapturedOfferQueue



def node(**changes):
    value = dict(shopId=123, itemId=456, productName='Fone <Bluetooth>',
                 imageUrl='https://down-br.img.susercontent.com/file/test',
                 priceMin='79.90', priceMax='99.90', priceDiscountRate=30,
                 ratingStar='4.8', sales=100, periodStartTime=int(time.time())-100,
                 periodEndTime=int(time.time())+1000)
    value.update(changes)
    return value


def result(nodes, more=False):
    return {'productOfferV2': {'nodes': nodes, 'pageInfo': {'hasNextPage': more}}}


class RadarTests(unittest.TestCase):
    def test_canonical_price_and_caption(self):
        offer = candidate(node())
        self.assertEqual(product(offer['url'])[0], offer['product_id'])
        self.assertEqual(offer['price'], '79,90')
        self.assertIn('a partir de', caption(offer))
        self.assertIn('&lt;Bluetooth&gt;', caption(offer))
        self.assertIsNone(offer['coupon'])

    def test_decimal_threshold_includes_exact_boundary(self):
        self.assertIsNotNone(candidate(node(ratingStar='4.9'), minimum_rating=4.9))
        self.assertIsNone(candidate(node(ratingStar='4.89'), minimum_rating=4.9))
        self.assertIsNotNone(candidate(node(ratingStar='4.91'), minimum_rating=4.9))
        self.assertIsNotNone(candidate(node(priceDiscountRate='50.1'), minimum_discount=50.1))
        self.assertIsNone(candidate(node(priceDiscountRate='50.09'), minimum_discount=50.1))

    def test_filters_and_bad_data(self):
        for bad in ({'priceDiscountRate': 19}, {'ratingStar': '4.4'}, {'sales': 49},
                    {'priceMin': 'NaN'}, {'priceMax': '1'}, {'shopId': -1},
                    {'imageUrl': 'https://example.org/photo'}, {'priceDiscountRate': 101}):
            with self.subTest(bad=bad):
                self.assertIsNone(candidate(node(**bad)))

    def test_period_is_required(self):
        for bad in ({'periodStartTime': 0}, {'periodEndTime': 0},
                    {'periodStartTime': int(time.time())+500}, {'periodEndTime': int(time.time())-1}):
            self.assertIsNone(candidate(node(**bad)))

    def test_pagination_dedup_and_keyword_escape(self):
        client = Mock()
        client.request.side_effect = [result([node()], True), result([node(), node(itemId=789)])]
        offers, scanned = collect(client, keyword='fone "azul"', pages=2)
        self.assertEqual((len(offers), scanned), (2, 3))
        self.assertIn(json.dumps('fone "azul"'), client.request.call_args_list[0].args[0])
        self.assertIn('page: 2', client.request.call_args_list[1].args[0])

    def test_refresh_rejects_changed_product_or_discount(self):
        client = Mock()
        for bad in (node(itemId=999), node(priceDiscountRate=5)):
            client.request.return_value = result([bad])
            with self.assertRaises(AffiliateError):
                refresh(client, candidate(node()))

    def test_refresh_updates_price(self):
        client = Mock()
        client.request.return_value = result([node(priceMin='80')])
        self.assertEqual(refresh(client, candidate(node()))['price'], '80,00')

    def test_no_link_generated_if_revalidation_fails(self):
        client = ShopeeAffiliate('123', 'secret', transport=Mock())
        client.request = Mock(return_value=result([node(priceDiscountRate=0)]))
        client.generate_link = Mock()
        with self.assertRaises(AffiliateError):
            client.prepare(candidate(node()))
        client.generate_link.assert_not_called()

    def test_publisher_reads_group_and_radar_from_sqlite(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            ledger = Ledger(base / 'publicacoes.sqlite3')
            intelligence = Intelligence(ledger.db)
            radar_offer = candidate(node(itemId=999))
            intelligence.enqueue([radar_offer], now=time.time())

            queue = CapturedOfferQueue(base / 'publicacoes.sqlite3')
            queue.replace_capture([{
                'product_id': 'telegram',
                'source': 'telegram',
                'store': 'Shopee',
                'kind': 'product_offer',
                'source_date': '2099-01-01T00:00:00+00:00',
                'chat_id': -123,
                'message_id': 1,
            }])

            import bot_ofertas_revisao as publisher
            with patch.object(publisher, 'BASE', base):
                rows = list(publisher.ordered_rows(intelligence, '@teste', queue))
            self.assertEqual(
                [x['product_id'] for x in rows],
                ['telegram', 'Shopee:123:999'],
            )
            queue.close()
            ledger.db.close()
