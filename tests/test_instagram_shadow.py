import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from instagram_adapter import InstagramDryRunAdapter
from multicanal_shadow import ShadowDistribution


class InstagramShadowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.image = Path(self.temp.name) / "offer.jpg"
        self.image.write_bytes(b"fake-image")
        self.offer = {
            "product_id": "Shopee:1:2",
            "store": "Shopee",
            "kind": "product_offer",
            "name": "Notebook Gamer",
            "price": "3.999,90",
            "price_condition": "no PIX",
            "coupon": "TESTE10",
            "url": "https://shopee.com.br/product/1/2",
            "note": "Origem https://s.shopee.com.br/telegram-extra",
            "affiliate_url": "https://s.shopee.com.br/telegram",
            "affiliate_generated": True,
        }

    def test_adapter_is_strictly_dry_run_and_strips_telegram_affiliate(self):
        adapter = InstagramDryRunAdapter()
        payload = adapter.prepare(self.offer, image=self.image)
        self.assertEqual(payload["state"], "READY")
        self.assertFalse(payload["publish_enabled"])
        self.assertEqual(payload["affiliate_state"], "DESTINATION_ATTRIBUTION_REQUIRED")
        self.assertNotIn("affiliate_url", payload["source_offer"])
        self.assertNotIn("url", payload["source_offer"])
        self.assertNotIn("https://", json.dumps(payload["source_offer"]))
        self.assertNotIn("s.shopee.com.br", payload["caption"])
        with self.assertRaises(RuntimeError):
            adapter.publish(payload)

    def test_missing_media_blocks_shadow_candidate(self):
        payload = InstagramDryRunAdapter().prepare(self.offer, image=None)
        self.assertEqual(payload["state"], "BLOCKED")
        self.assertIn("MIDIA_INDISPONIVEL", payload["reasons"])
    def test_shadow_records_real_candidate_without_network_publish(self):
        db = sqlite3.connect(":memory:")
        shadow = ShadowDistribution(db, now=lambda: 1234.0)
        result = shadow.mirror_success(
            self.offer,
            day="2026-10-02",
            source_external_id="1777",
            image=self.image,
            destinations=("instagram",),
        )
        self.assertEqual(result["instagram"]["state"], "READY")
        row = db.execute(
            "SELECT destination,state,source_external_id,payload FROM delivery_shadow"
        ).fetchone()
        self.assertEqual(row[:3], ("instagram", "READY", "1777"))
        payload = json.loads(row[3])
        self.assertFalse(payload["publish_enabled"])
        self.assertNotIn("affiliate_url", payload["source_offer"])
        self.assertEqual(shadow.counts(), {("instagram", "READY"): 1})
        db.close()

    def test_unknown_shadow_destination_fails_closed(self):
        db = sqlite3.connect(":memory:")
        shadow = ShadowDistribution(db)
        result = shadow.mirror_success(
            self.offer,
            day="2026-10-02",
            source_external_id="1777",
            image=self.image,
            destinations=("unknown-network",),
        )
        self.assertEqual(result["unknown-network"]["state"], "BLOCKED")
        self.assertFalse(result["unknown-network"]["publish_enabled"])
        self.assertEqual(
            db.execute("SELECT COUNT(*) FROM delivery_shadow").fetchone()[0], 0
        )
        db.close()


if __name__ == "__main__":
    unittest.main()
