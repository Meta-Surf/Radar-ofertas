import sqlite3
import unittest

from distribuicao import DeliveryOutbox, normalize_destination, payload_revision


class DeliveryOutboxTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.outbox = DeliveryOutbox(self.db, now=lambda: 1234.5)

    def tearDown(self):
        self.db.close()

    def test_external_id_is_text_and_destinations_are_independent(self):
        offer = {"product_id": "Shopee:1:2", "price": "99,90"}
        self.outbox.record_sent(
            offer["product_id"], destination="telegram", account="@canal",
            surface="channel", day="2026-10-02", external_id=123, payload=offer,
        )
        self.outbox.record_sent(
            offer["product_id"], destination="instagram", account="perfil",
            surface="feed", day="2026-10-02", external_id="IG_ABC_123", payload=offer,
        )
        rows = self.db.execute(
            "SELECT destination,external_id,state FROM deliveries ORDER BY destination"
        ).fetchall()
        self.assertEqual(rows, [
            ("instagram", "IG_ABC_123", "SENT"),
            ("telegram", "123", "SENT"),
        ])

    def test_uncertain_can_be_reconciled_to_sent(self):
        offer = {"product_id": "MercadoLivre:7", "price": "10,00"}
        self.outbox.record_uncertain(
            offer["product_id"], destination="telegram", account="@canal",
            surface="channel", day="2026-10-02", payload=offer, reason="timeout",
        )
        self.outbox.record_sent(
            offer["product_id"], destination="telegram", account="@canal",
            surface="channel", day="2026-10-02", external_id="55", payload=offer,
        )
        row = self.db.execute(
            "SELECT state,external_id,error_reason FROM deliveries"
        ).fetchone()
        self.assertEqual(row, ("SENT", "55", ""))

    def test_uncertain_never_downgrades_confirmed_sent(self):
        offer = {"product_id": "P:sent"}
        self.outbox.record_sent(
            'P:sent', destination='telegram', account='@canal', surface='channel',
            day='2026-10-02', external_id='88', payload=offer,
        )
        self.outbox.record_uncertain(
            'P:sent', destination='telegram', account='@canal', surface='channel',
            day='2026-10-02', payload=offer, reason='timeout tardio',
        )
        self.assertEqual(
            self.db.execute(
                "SELECT state,external_id FROM deliveries WHERE product='P:sent'"
            ).fetchone(),
            ('SENT', '88'),
        )

    def test_revision_is_stable(self):
        self.assertEqual(payload_revision({"b": 2, "a": 1}),
                         payload_revision({"a": 1, "b": 2}))
    def test_backfill_is_idempotent_and_does_not_overwrite_payload(self):
        self.db.execute(
            "CREATE TABLE posts (product TEXT,day TEXT,status TEXT,message_id INTEGER,updated_at REAL)"
        )
        self.db.execute(
            "INSERT INTO posts VALUES ('P:sent','2026-10-02','sent',77,1000)"
        )
        self.assertEqual(self.outbox.backfill_telegram_posts('@canal'), 1)
        self.outbox.record_sent(
            'P:sent', destination='telegram', account='@canal', surface='channel',
            day='2026-10-02', external_id='77', payload={'name':'enriched'},
        )
        self.assertEqual(self.outbox.backfill_telegram_posts('@canal'), 0)
        payload = self.db.execute(
            "SELECT payload FROM deliveries WHERE product='P:sent'"
        ).fetchone()[0]
        self.assertIn('enriched', payload)

    def test_outbox_claim_and_retry_are_destination_scoped(self):
        offer = {"product_id": "Shopee:9:9", "price": "19,90"}
        self.outbox.enqueue(
            offer["product_id"], destination="instagram", account="perfil",
            surface="feed", day="2026-10-02", payload=offer,
        )
        row = self.outbox.ready("instagram")[0]
        delivery_id = row[0]
        self.assertTrue(self.outbox.claim(delivery_id))
        self.assertTrue(self.outbox.retry(delivery_id, delay=60, reason="rate limit"))
        self.assertEqual(self.outbox.ready("instagram"), [])
        state = self.db.execute(
            "SELECT state,attempts,error_reason FROM deliveries WHERE id=?",
            (delivery_id,),
        ).fetchone()
        self.assertEqual(state, ("RETRY", 1, "rate limit"))

    def test_destination_normalization(self):
        self.assertEqual(normalize_destination("Whats App"), "whats-app")
        with self.assertRaises(ValueError):
            normalize_destination("")


if __name__ == "__main__":
    unittest.main()
