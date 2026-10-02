import sqlite3
import unittest

import reconciliar_reservas as reconcile
from distribuicao import DeliveryOutbox


class ReconcileDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute(
            "CREATE TABLE posts (product TEXT, day TEXT, status TEXT, message_id INTEGER, "
            "reserved_at REAL, send_started_at REAL, updated_at REAL, "
            "PRIMARY KEY(product,day))"
        )
        self.outbox = DeliveryOutbox(self.db)

    def tearDown(self):
        self.db.close()

    def add_uncertain(self, product="P:1", day="2026-10-02"):
        self.db.execute(
            "INSERT INTO posts(product,day,status) VALUES (?,?,?)",
            (product, day, "uncertain"),
        )
        self.outbox.record_uncertain(
            product, destination="telegram", account=reconcile.TELEGRAM_CHANNEL,
            surface="channel", day=day, reason="legacy",
        )
    def test_not_sent_removes_legacy_and_shadow_uncertain(self):
        self.add_uncertain()
        reconcile.resolve_not_sent(self.db, "P:1", "2026-10-02")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 0)
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0], 0
        )

    def test_sent_reconciles_shadow_delivery_with_text_external_id(self):
        self.add_uncertain()
        reconcile.resolve_sent(self.db, "P:1", "2026-10-02", 630)
        self.assertEqual(
            self.db.execute(
                "SELECT status,message_id FROM posts WHERE product='P:1'"
            ).fetchone(),
            ("sent", 630),
        )
        self.assertEqual(
            self.db.execute(
                "SELECT state,external_id FROM deliveries WHERE product='P:1'"
            ).fetchone(),
            ("SENT", "630"),
        )


if __name__ == "__main__":
    unittest.main()
