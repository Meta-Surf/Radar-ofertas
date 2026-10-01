import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from inteligencia_ofertas import Intelligence
from prepublicacao import GateReject, PrePublicationGate


SHOPEE_LINK = "https://s.shopee.com.br/abc123"
KABUM_LINK = (
    "https://www.awin1.com/pclick.php?"
    "p=1&a=3106767&m=17729"
)
ML_LINK = "https://meli.la/abc123"


def shopee_offer(price="100,00", source="shopee_api", age=0):
    stamp = datetime.now(timezone.utc) - timedelta(seconds=age)
    return {
        "product_id": "Shopee:1:2",
        "store": "Shopee",
        "url": "https://shopee.com.br/product/1/2",
        "source": source,
        "source_date": stamp.isoformat(),
        "price": price,
        "price_from": False,
        "affiliate_generated": True,
        "affiliate_url": SHOPEE_LINK,
        "variant_id": "2",
        "variant_verified": True,
    }


def ml_offer(price="100,00"):
    return {
        "kind": "ml_offer",
        "product_id": "MercadoLivre:123",
        "store": "Mercado Livre",
        "url": "https://produto.mercadolivre.com.br/MLB-123-_JM",
        "source": "telegram",
        "source_date": datetime.now(timezone.utc).isoformat(),
        "price": price,
        "price_from": False,
        "affiliate_generated": True,
        "affiliate_url": ML_LINK,
    }


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.db = sqlite3.connect(":memory:")
        self.db.execute(
            "CREATE TABLE posts (product TEXT, day TEXT, status TEXT, message_id INTEGER, "
            "PRIMARY KEY(product, day))"
        )
        self.kabum = SimpleNamespace(publisher_id="3106767")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def gate(self, **kwargs):
        return PrePublicationGate(
            self.base, self.db, kabum_affiliate=self.kabum, **kwargs
        )
    def test_shopee_price_increase_blocks(self):
        gate = self.gate()
        original = shopee_offer("100,00")
        prepared = shopee_offer("110,00")
        with self.assertRaises(GateReject) as ctx:
            gate.validate(original, prepared, "@canal")
        self.assertEqual(ctx.exception.reason, "PRECO_AUMENTOU")
        self.assertTrue(ctx.exception.discard)

    def test_shopee_lower_price_updates_and_passes(self):
        gate = self.gate()
        original = shopee_offer("100,00")
        prepared = shopee_offer("90,00")
        result = gate.validate(original, prepared, "@canal")
        self.assertEqual(result["price"], "90,00")
        self.assertEqual(
            result["prepublication_validation"], "PRECO_ATUALIZADO_MENOR"
        )
        row = self.db.execute(
            "SELECT status,reason FROM prepublication_gate ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(row, ("ATUALIZADA", "PRECO_ATUALIZADO_MENOR"))

    def test_old_group_shopee_price_without_strong_revalidation_blocks(self):
        gate = self.gate()
        original = shopee_offer("100,00", source="telegram", age=600)
        prepared = dict(original)
        with self.assertRaises(GateReject) as ctx:
            gate.validate(original, prepared, "@canal")
        self.assertEqual(ctx.exception.reason, "PRECO_NAO_REVALIDADO")

    def test_invalid_affiliate_link_blocks(self):
        gate = self.gate()
        offer = shopee_offer()
        offer["affiliate_url"] = "https://example.com/not-affiliate"
        with self.assertRaises(GateReject) as ctx:
            gate.validate(offer, offer, "@canal")
        self.assertEqual(ctx.exception.reason, "LINK_INVALIDO")
    def test_ml_fresh_price_increase_blocks(self):
        reader = Mock()
        reader.read.return_value = {
            **ml_offer("120,00"),
            "resolved_url": "https://produto.mercadolivre.com.br/MLB-123-_JM",
            "api_image": "https://http2.mlstatic.com/test.jpg",
            "name": "Produto ML",
        }
        gate = self.gate(ml_reader=reader)
        original = ml_offer("100,00")
        prepared = ml_offer("100,00")
        with self.assertRaises(GateReject) as ctx:
            gate.validate(original, prepared, "@canal")
        self.assertEqual(ctx.exception.reason, "PRECO_AUMENTOU")

    def _kabum_db(self, price_cents=9000, stock=""):
        db = sqlite3.connect(self.base / "kabum_historico.sqlite3")
        db.execute(
            """CREATE TABLE kabum_products(
            product_id TEXT PRIMARY KEY,name TEXT,price_cents INTEGER,
            affiliate_url TEXT,image_url TEXT,in_stock TEXT,last_seen REAL)"""
        )
        db.execute(
            "INSERT INTO kabum_products VALUES (?,?,?,?,?,?,?)",
            (
                "123", "Produto KaBuM", price_cents, KABUM_LINK,
                "https://images.example.com/123.jpg", stock, time.time(),
            ),
        )
        db.commit()
        db.close()

    def kabum_offer(self, price="100,00"):
        return {
            "product_id": "KaBuM:123",
            "store": "KaBuM",
            "url": "https://www.kabum.com.br/produto/123",
            "source": "kabum_feed",
            "source_date": datetime.now(timezone.utc).isoformat(),
            "price": price,
            "price_from": False,
            "affiliate_generated": True,
            "affiliate_url": KABUM_LINK,
        }

    def test_kabum_lower_feed_price_updates(self):
        self._kabum_db(9000)
        gate = self.gate()
        original = self.kabum_offer("100,00")
        result = gate.validate(original, dict(original), "@canal")
        self.assertEqual(result["price"], "90,00")
        self.assertEqual(result["prepublication_validation"], "PRECO_ATUALIZADO_MENOR")

    def test_kabum_explicit_out_of_stock_blocks(self):
        self._kabum_db(10000, "out_of_stock")
        gate = self.gate()
        offer = self.kabum_offer("100,00")
        with self.assertRaises(GateReject) as ctx:
            gate.validate(offer, dict(offer), "@canal")
        self.assertEqual(ctx.exception.reason, "SEM_ESTOQUE_COMPROVADO")
    def test_duplicate_kabum_can_repeat_only_after_lower_price(self):
        self._kabum_db(9000)
        gate = self.gate()
        intelligence = Intelligence(self.db)
        old = self.kabum_offer("100,00")
        old["variant_id"] = "123"
        old["variant_verified"] = True
        intelligence.record(old, "@canal", 10, now=time.time() - 2 * 86400)
        self.db.execute(
            "INSERT INTO posts VALUES (?,?,?,?)",
            ("KaBuM:123", "2026-09-29", "sent", 10),
        )
        self.db.commit()

        current = self.kabum_offer("100,00")
        result = gate.validate(current, dict(current), "@canal")
        self.assertEqual(result["price"], "90,00")

    def test_duplicate_same_price_blocks(self):
        self._kabum_db(10000)
        gate = self.gate()
        intelligence = Intelligence(self.db)
        old = self.kabum_offer("100,00")
        intelligence.record(old, "@canal", 11, now=time.time() - 2 * 86400)
        self.db.execute(
            "INSERT INTO posts VALUES (?,?,?,?)",
            ("KaBuM:123", "2026-09-29", "sent", 11),
        )
        self.db.commit()
        with self.assertRaises(GateReject) as ctx:
            gate.validate(old, dict(old), "@canal")
        self.assertEqual(ctx.exception.reason, "DUPLICADA")

    def kabum_coupon_item(self):
        now = datetime.now(timezone.utc)
        return {
            "promotionId": 999,
            "type": "voucher",
            "advertiser": {"id": 17729, "joined": True},
            "title": "10% OFF em selecionados",
            "description": "10% OFF em selecionados",
            "terms": ".",
            "startDate": (now - timedelta(hours=1)).isoformat(),
            "endDate": (now + timedelta(days=1)).isoformat(),
            "url": "https://www.kabum.com.br/promocao/TESTE10",
            "urlTracking": KABUM_LINK,
            "voucher": {"code": "TESTE10"},
        }

    def test_kabum_coupon_is_reloaded_from_offers_api(self):
        import cupons_kabum
        item = self.kabum_coupon_item()
        prepared = cupons_kabum.alert_from_offer(item, self.kabum)
        api = Mock(enabled=True)
        api.offers.return_value = [item]
        gate = self.gate()
        with patch("awin_kabum.AwinKabumAPI.from_env", return_value=api):
            result = gate.validate(prepared, dict(prepared), "@canal")
        self.assertEqual(result["product_id"], "KaBuMCoupon:999")
        self.assertEqual(result["code"], "TESTE10")

    def test_kabum_coupon_missing_from_api_blocks_as_inactive(self):
        import cupons_kabum
        item = self.kabum_coupon_item()
        prepared = cupons_kabum.alert_from_offer(item, self.kabum)
        api = Mock(enabled=True)
        api.offers.return_value = []
        gate = self.gate()
        with patch("awin_kabum.AwinKabumAPI.from_env", return_value=api):
            with self.assertRaises(GateReject) as ctx:
                gate.validate(prepared, dict(prepared), "@canal")
        self.assertEqual(ctx.exception.reason, "CUPOM_INATIVO")

    def amazon_offer(self, price="100,00"):
        return {
            "product_id": "Amazon:B012345678",
            "store": "Amazon",
            "url": "https://www.amazon.com.br/dp/B012345678",
            "source": "telegram",
            "source_date": datetime.now(timezone.utc).isoformat(),
            "price": price,
            "price_from": False,
            "affiliate_generated": True,
            "affiliate_url": "https://www.amazon.com.br/dp/B012345678?tag=minhatag-20",
            "stock_confirmed": True,
        }

    def test_amazon_lower_api_price_updates(self):
        amazon = SimpleNamespace(partner_tag="minhatag-20")
        gate = PrePublicationGate(
            self.base, self.db, kabum_affiliate=self.kabum,
            amazon_affiliate=amazon,
        )
        original = self.amazon_offer("100,00")
        prepared = self.amazon_offer("90,00")
        result = gate.validate(original, prepared, "@canal")
        self.assertEqual(result["price"], "90,00")
        self.assertEqual(result["prepublication_validation"], "PRECO_ATUALIZADO_MENOR")

    def test_amazon_price_increase_blocks(self):
        amazon = SimpleNamespace(partner_tag="minhatag-20")
        gate = PrePublicationGate(
            self.base, self.db, kabum_affiliate=self.kabum,
            amazon_affiliate=amazon,
        )
        original = self.amazon_offer("100,00")
        prepared = self.amazon_offer("110,00")
        with self.assertRaises(GateReject) as ctx:
            gate.validate(original, prepared, "@canal")
        self.assertEqual(ctx.exception.reason, "PRECO_AUMENTOU")


if __name__ == "__main__":
    unittest.main()
