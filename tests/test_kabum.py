import csv
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import radar_kabum as kabum


def product_feed(price="100.00"):
    rows = [
        ["aw_deep_link","product_name","merchant_product_id","search_price","brand_name","merchant_product_category_path"],
        ["https://www.kabum.com.br/produto/123?awc=teste","Produto Teste","123",price,"Marca","Hardware"],
    ]
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerows(rows)
    return stream.getvalue().encode()


def feed_list():
    rows = [
        ["Advertiser ID","Advertiser Name","Feed ID","Feed Name","Language","URL"],
        ["17729","Kabum BR","11111","Outro feed","Portuguese","https://example.test/outro.csv.gz"],
        ["17729","Kabum BR","46967","Kabum BR Datafeed","Portuguese","https://example.test/kabum.csv.gz"],
    ]
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerows(rows)
    return stream.getvalue().encode()


class KabumFeedTests(unittest.TestCase):
    def test_money_uses_decimal_without_float_guessing(self):
        self.assertEqual(kabum.money("123.45"), 12345)
        self.assertEqual(kabum.money("123,45"), 12345)
        self.assertIsNone(kabum.money(""))
        self.assertIsNone(kabum.money("0"))

    def test_product_feed_is_recognized(self):
        self.assertTrue(kabum.is_product_feed(product_feed()))

    def test_feed_list_prioritizes_configured_feed_id(self):
        with patch.dict("os.environ", {
            "KABUM_AWIN_ADVERTISER_ID": "17729",
            "KABUM_AWIN_FEED_ID": "46967",
            "KABUM_AWIN_LANGUAGE": "pt_BR",
        }, clear=False):
            selected = kabum.find_product_url_in_feed_list(feed_list())
        self.assertEqual(selected["feed_id"], "46967")
        self.assertEqual(selected["advertiser_id"], "17729")
        self.assertEqual(selected["url"], "https://example.test/kabum.csv.gz")

    def test_feed_list_download_is_validated_before_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "kabum_feed_atual.csv.gz"
            with patch.object(kabum, "fetch_url", return_value=product_feed()):
                raw, source = kabum.resolve_downloaded_raw(
                    feed_list(), cache, timeout=5, attempts=1
                )
            self.assertTrue(kabum.is_product_feed(raw))
            self.assertTrue(cache.is_file())
            self.assertIn("Feed ID 46967", source)

    def test_ingest_tracks_new_product_then_price_change_and_drop(self):
        db = kabum.init_db(":memory:")
        try:
            total, valid, new_products, changes = kabum.ingest(product_feed("100.00"), db)
            self.assertEqual((total, valid, new_products, changes), (1, 1, 1, 0))

            total, valid, new_products, changes = kabum.ingest(product_feed("80.00"), db)
            self.assertEqual((total, valid, new_products, changes), (1, 1, 0, 1))

            drops = kabum.drops(db, minimum_pct=5, limit=10)
            self.assertEqual(len(drops), 1)
            self.assertEqual(drops[0][0], "123")
            self.assertEqual(drops[0][3], 8000)
            self.assertEqual(drops[0][6], 10000)
            self.assertAlmostEqual(drops[0][7], 20.0)
        finally:
            db.close()

    def test_cache_fallback_is_used_when_download_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            cache = base / kabum.DEFAULT_CACHE
            cache.write_bytes(product_feed())
            args = type("Args", (), {
                "arquivo": None,
                "url": "https://example.test/list.csv",
                "sem_fallback": False,
            })()
            with patch.object(kabum, "fetch_url", side_effect=RuntimeError("falha simulada")):
                raw, source = kabum.resolve_feed(args, base)
            self.assertTrue(kabum.is_product_feed(raw))
            self.assertIn("cache local", source)


if __name__ == "__main__":
    unittest.main()
