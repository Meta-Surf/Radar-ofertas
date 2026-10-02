import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import cupons_kabum as ck
from inteligencia_ofertas import Intelligence


NOW = datetime(2026, 10, 1, 20, 0, tzinfo=timezone.utc)
TRACK = (
    "https://www.awin1.com/cread.php?"
    "awinmid=17729&awinaffid=3106767&ued=https%3A%2F%2Fwww.kabum.com.br"
)


def voucher(pid="4130272", code="EQUIPA20", days_old=0, hours_left=48,
            url="https://www.kabum.com.br/promocao/CP1EQUIPA20"):
    start = NOW - timedelta(days=days_old, hours=1)
    end = NOW + timedelta(hours=hours_left)
    return {
        "promotionId": int(pid),
        "type": "voucher",
        "advertiser": {"id": 17729, "joined": True},
        "title": f"20% OFF em selecionados com o cupom {code}.",
        "description": f"20% OFF em selecionados com o cupom {code}.",
        "terms": ".",
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "url": url,
        "urlTracking": TRACK,
        "voucher": {"code": code},
    }
class KabumCouponTests(unittest.TestCase):
    def test_recent_generic_voucher_is_eligible(self):
        self.assertTrue(ck.eligible(voucher(), NOW))

    def test_old_active_but_stale_voucher_is_blocked(self):
        self.assertFalse(ck.eligible(voucher(days_old=365, hours_left=8000), NOW))

    def test_product_specific_voucher_is_not_generic(self):
        item = voucher(url="https://www.kabum.com.br/produto/7070/teste")
        self.assertFalse(ck.eligible(item, NOW))

    def test_expired_voucher_is_blocked(self):
        item = voucher(hours_left=-1)
        self.assertFalse(ck.eligible(item, NOW))

    def test_alert_uses_promotion_id_for_identity_and_official_tracking(self):
        affiliate = Mock()
        alert = ck.alert_from_offer(voucher(), affiliate, NOW)
        self.assertEqual(alert["product_id"], "KaBuMCoupon:4130272")
        self.assertEqual(alert["affiliate_url"], TRACK)
        self.assertEqual(alert["source"], "kabum_awin_coupon")
        affiliate._link_builder.assert_not_called()

    def test_prepare_attribution_is_destination_specific(self):
        affiliate = Mock()
        alert = ck.alert_from_offer(voucher(), affiliate, NOW)
        prepared = ck.prepare_alert(
            affiliate, alert, now=NOW, destination='instagram'
        )
        self.assertIn('clickref=instagram', prepared['affiliate_url'])
        affiliate._link_builder.assert_not_called()

    def test_caption_removes_embedded_url_and_keeps_code_and_validity(self):
        item = voucher()
        item["title"] += " https://www.kabum.com.br/promocao/teste"
        alert = ck.alert_from_offer(item, Mock(), NOW)
        caption = ck.alert_caption(alert)
        self.assertNotIn("/promocao/teste", caption)
        self.assertIn("<code>EQUIPA20</code>", caption)
        self.assertIn("horário de Brasília", caption)
    def test_radar_queue_accepts_coupon_without_price_and_deduplicates(self):
        db = sqlite3.connect(":memory:")
        try:
            db.execute(
                "CREATE TABLE posts (product TEXT, day TEXT, status TEXT, message_id INTEGER, "
                "PRIMARY KEY(product, day))"
            )
            intelligence = Intelligence(db)
            alert = ck.alert_from_offer(voucher(), Mock(), NOW)
            now_ts = NOW.timestamp()
            intelligence.enqueue([alert], now=now_ts)
            intelligence.enqueue([alert], now=now_ts + 10)
            rows = db.execute(
                "SELECT product,payload FROM radar_queue WHERE product=?",
                ("KaBuMCoupon:4130272",),
            ).fetchall()
            self.assertEqual(len(rows), 1)
            pending = intelligence.pending("@canal", now=now_ts + 10)
            self.assertEqual(pending[0]["product_id"], "KaBuMCoupon:4130272")
        finally:
            db.close()

    def test_coupon_has_priority_over_normal_kabum_candidate(self):
        db = sqlite3.connect(":memory:")
        try:
            db.execute(
                "CREATE TABLE posts (product TEXT, day TEXT, status TEXT, message_id INTEGER, "
                "PRIMARY KEY(product, day))"
            )
            intelligence = Intelligence(db)
            alert = ck.alert_from_offer(voucher(), Mock(), NOW)
            product_offer = {
                "product_id": "KaBuM:123",
                "source": "kabum_feed",
                "store": "KaBuM",
                "price": "999,90",
                "radar_score": 100,
                "variant_id": "123",
                "variant_verified": True,
            }
            intelligence.enqueue([product_offer, alert], now=NOW.timestamp())
            pending = intelligence.pending("@canal", now=NOW.timestamp())
            self.assertEqual(pending[0]["source"], "kabum_awin_coupon")
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
