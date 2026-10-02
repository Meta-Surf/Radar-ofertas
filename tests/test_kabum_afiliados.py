import html
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import canal_espelho as mirror
import bot_ofertas_revisao as publisher
import publicacao_oferta as offer_publisher
from kabum_afiliados import KabumAffiliate, valid_affiliate_url
from ofertas_core import product, safe_url
from shopee_afiliados import AffiliateError


AWIN = "https://www.awin1.com/pclick.php?p=42173320107&a=3106767&m=17729"


def make_db(path):
    db = sqlite3.connect(path)
    db.execute(
        """CREATE TABLE kabum_products(
        product_id TEXT PRIMARY KEY, name TEXT, brand TEXT, category TEXT,
        price_cents INTEGER, affiliate_url TEXT, image_url TEXT,
        in_stock TEXT, last_seen REAL, payload TEXT)"""
    )
    db.execute(
        "INSERT INTO kabum_products VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("7070", "Cabo USB", "", "", 1990, AWIN, "", "1", 1, "{}"),
    )
    db.commit()
    db.close()


class KabumAffiliateTests(unittest.TestCase):
    def test_kabum_product_url_is_recognized_by_id(self):
        url = "https://www.kabum.com.br/produto/7070/cabo-usb?utm_source=grupo"
        self.assertTrue(safe_url(url))
        self.assertEqual(
            product(url),
            ("KaBuM:7070", "KaBuM", "https://www.kabum.com.br/produto/7070"),
        )
        self.assertTrue(mirror.supported_store_url(url))

    def test_prepare_uses_awin_link_from_local_feed(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "kabum_historico.sqlite3"
            make_db(db_path)
            affiliate = KabumAffiliate(db_path)
            offer = {
                "product_id": "KaBuM:7070",
                "store": "KaBuM",
                "url": "https://www.kabum.com.br/produto/7070",
                "price": "19,90",
            }
            prepared = affiliate.prepare(offer)
            self.assertEqual(prepared["affiliate_url"], AWIN)
            self.assertTrue(prepared["affiliate_generated"])
            self.assertTrue(valid_affiliate_url(prepared["affiliate_url"]))

    def test_sender_accepts_only_valid_kabum_awin_link(self):
        offer = {
            "kind": "product_offer",
            "store": "KaBuM",
            "publish_mode": "mirror",
            "affiliate_generated": True,
            "affiliate_url": AWIN,
            "mirror_template": "🔥 Oferta\nLink: " + mirror.PLACEHOLDER,
            "mirror_button_text": None,
        }
        response = Mock()
        response.json.return_value = {"ok": True, "result": {"message_id": 77}}
        with patch.object(offer_publisher.requests, "post", return_value=response) as post:
            message_id, wait = publisher.send("token", "@destino", offer, None)
        self.assertEqual((message_id, wait), (77, 0))
        self.assertIn(html.escape(AWIN, quote=True), post.call_args.kwargs["data"]["text"])

    def test_missing_product_never_falls_back_to_plain_kabum_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "kabum_historico.sqlite3"
            make_db(db_path)
            affiliate = KabumAffiliate(db_path)
            offer = {
                "product_id": "KaBuM:518299",
                "store": "KaBuM",
                "url": "https://www.kabum.com.br/produto/518299",
                "price": "999,90",
            }
            with self.assertRaises(AffiliateError):
                affiliate.prepare(offer)


if __name__ == "__main__":
    unittest.main()
