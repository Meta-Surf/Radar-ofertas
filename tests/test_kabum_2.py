import csv
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import radar_kabum as kabum
from awin_kabum import AwinKabumAPI, stock_allows_publication
from inteligencia_ofertas import Intelligence, comparison_key
from kabum_afiliados import KabumAffiliate, valid_affiliate_url


DAY = 86400


def feed(price, brand="NVIDIA", category="Placas de video"):
    rows = [
        ["aw_deep_link", "product_name", "merchant_product_id", "search_price",
         "brand_name", "merchant_product_category_path", "merchant_image_url"],
        ["https://www.awin1.com/pclick.php?p=1&a=3106767&m=17729",
         "Placa NVIDIA Teste", "123", price, brand, category,
         "https://images.example.test/produto.jpg"],
    ]
    stream = io.StringIO()
    csv.writer(stream).writerows(rows)
    return stream.getvalue().encode()


class Kabum20Tests(unittest.TestCase):
    def test_history_uses_15_day_steps_and_candidate_ranking(self):
        now = 2_000_000_000
        db = kabum.init_db(":memory:")
        try:
            with patch.object(kabum.time, "time", return_value=now - 50 * DAY):
                kabum.ingest(feed("100.00"), db)
            with patch.object(kabum.time, "time", return_value=now - 20 * DAY):
                kabum.ingest(feed("90.00"), db)
            with patch.object(kabum.time, "time", return_value=now):
                kabum.ingest(feed("80.00"), db)

            self.assertEqual(kabum.history_window_days(db, "123", 8000, now), 45)
            official = {
                "KaBuM:123": [{
                    "type": "voucher", "title": "Oferta oficial",
                    "coupon": "KABUM10"
                }]
            }
            candidates = kabum.production_candidates(
                db, minimum_pct=5, limit=10, max_age_hours=24,
                official_offers=official, now=now,
            )
            self.assertEqual(len(candidates), 1)
            offer = candidates[0]
            self.assertEqual(offer["kabum_history_days"], 45)
            self.assertIn("45 dias", offer["history_badge"])
            self.assertTrue(offer["kabum_brand_priority"])
            self.assertTrue(offer["kabum_category_priority"])
            self.assertEqual(offer["coupon"], "KABUM10")
            self.assertEqual(offer["source"], "kabum_feed")
        finally:
            db.close()
    def test_ingest_promotes_official_kabum_http_image_to_https(self):
        rows = [
            ['aw_deep_link','product_name','merchant_product_id','search_price',
             'merchant_image_url','aw_image_url'],
            ['https://www.awin1.com/pclick.php?p=1&a=3106767&m=17729',
             'Produto KaBuM','123','99.90',
             'http://images0.kabum.com.br/produtos/fotos/123/produto.jpg',
             'https://images2.productserve.com/produto.jpg'],
        ]
        stream = io.StringIO(); csv.writer(stream).writerows(rows)
        db = kabum.init_db(':memory:')
        try:
            kabum.ingest(stream.getvalue().encode(), db)
            image = db.execute(
                'SELECT image_url FROM kabum_products WHERE product_id=?', ('123',)
            ).fetchone()[0]
            self.assertEqual(
                image,
                'https://images0.kabum.com.br/produtos/fotos/123/produto.jpg',
            )
        finally:
            db.close()

    def test_accessory_category_does_not_receive_priority_bonus(self):
        self.assertFalse(
            kabum._category_priority(
                'Suporte Fixo para TVs',
                'Periféricos > Suportes > Suporte para TV',
            )
        )
        self.assertTrue(
            kabum._category_priority(
                'Monitor Gamer 27',
                'Monitores > Monitor Gamer',
            )
        )

    def test_explicit_out_of_stock_blocks_but_unknown_does_not(self):
        now = 2_000_000_000
        db = kabum.init_db(":memory:")
        try:
            with patch.object(kabum.time, "time", return_value=now - DAY):
                kabum.ingest(feed("100.00"), db)
            with patch.object(kabum.time, "time", return_value=now):
                kabum.ingest(feed("80.00"), db)
            blocked = kabum.production_candidates(
                db, availability={"123": "out_of_stock"}, now=now
            )
            allowed = kabum.production_candidates(db, availability={}, now=now)
            self.assertEqual(blocked, [])
            self.assertEqual(len(allowed), 1)
            self.assertTrue(stock_allows_publication(""))
        finally:
            db.close()

    def test_kabum_comparison_key_allows_lower_price_repeat_logic(self):
        db = sqlite3.connect(":memory:")
        try:
            intelligence = Intelligence(db)
            original = {
                "product_id": "KaBuM:123", "store": "KaBuM",
                "source": "kabum_feed", "price": "100,00",
            }
            lower = dict(original, price="90,00")
            self.assertEqual(comparison_key(original), comparison_key(lower))
            intelligence.record(original, "@canal", 1, now=1000)
            self.assertFalse(intelligence.can_repeat(lower, "@canal", now=1000 + DAY - 1))
            self.assertTrue(intelligence.can_repeat(lower, "@canal", now=1000 + DAY + 1))
        finally:
            db.close()

    def test_link_builder_fallback_generates_and_caches_only_awin(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kabum.sqlite3"
            db = kabum.init_db(path)
            db.close()
            client = KabumAffiliate(
                path, publisher_id="3106767", access_token="secret"
            )
            response = Mock(status_code=200)
            generated = (
                "https://www.awin1.com/cread.php?awinmid=17729&"
                "awinaffid=3106767&ued=https%3A%2F%2Fwww.kabum.com.br"
            )
            response.json.return_value = {"url": generated}
            with patch("kabum_afiliados.requests.post", return_value=response) as post:
                link = client.lookup(
                    "518299", "https://www.kabum.com.br/produto/518299"
                )
            self.assertEqual(link, generated)
            self.assertTrue(valid_affiliate_url(link))
            payload = post.call_args.kwargs["json"]
            self.assertEqual(payload["advertiserId"], 17729)
            self.assertEqual(
                payload["destinationUrl"],
                "https://www.kabum.com.br/produto/518299",
            )
            # Segunda consulta usa cache local, sem nova chamada.
            with patch("kabum_afiliados.requests.post") as second:
                self.assertEqual(
                    client.lookup("518299", "https://www.kabum.com.br/produto/518299"),
                    generated,
                )
                second.assert_not_called()
    def test_offers_api_keeps_only_joined_kabum_product_voucher(self):
        api = AwinKabumAPI("3106767", "secret")
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = [{
            "promotionId": 9,
            "type": "voucher",
            "advertiser": {"id": 17729, "name": "Kabum", "joined": True},
            "title": "Cupom oficial",
            "url": "https://www.kabum.com.br/produto/123/teste",
            "urlTracking": "https://www.awin1.com/pclick.php?p=1&a=2&m=17729",
            "voucher": {"code": "OFICIAL10", "exclusive": False},
        }]
        with patch("awin_kabum.requests.post", return_value=response):
            mapped = api.product_offer_map()
        self.assertEqual(mapped["KaBuM:123"][0]["coupon"], "OFICIAL10")
        self.assertEqual(mapped["KaBuM:123"][0]["promotion_id"], 9)

    def test_enhanced_feed_availability_is_parsed(self):
        api = AwinKabumAPI("3106767", "secret")
        response = Mock(status_code=200)
        response.raise_for_status.return_value = None
        response.text = (
            '{"id":"123","availability":"in_stock"}\n'
            '{"product_basic":{"id":"456","availability":"out_of_stock"}}\n'
        )
        with patch("awin_kabum.requests.get", return_value=response):
            states = api.enhanced_availability()
        self.assertEqual(states["123"], "in_stock")
        self.assertEqual(states["456"], "out_of_stock")


if __name__ == "__main__":
    unittest.main()
