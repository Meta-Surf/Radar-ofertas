import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fila_ofertas_sqlite import CapturedOfferQueue


class Clock:
    def __init__(self, value=1000):
        self.value = float(value)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def offer(product, *, chat=-123, message=7, price="10,00", digest="d1", recovered=False, stamp=1000):
    return {
        "product_id": product,
        "source": "telegram",
        "store": "Shopee",
        "kind": "product_offer",
        "chat_id": chat,
        "message_id": message,
        "price": price,
        "capture_digest": digest,
        "recovered": recovered,
        "source_date": datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
    }


class CapturedOfferQueueTests(unittest.TestCase):
    def test_edit_replaces_previous_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = Clock(1000)
            queue = CapturedOfferQueue(Path(directory) / "db.sqlite3", now=clock)
            queue.replace_capture([offer("p1", price="10,00", digest="old")], now=1000)
            queue.replace_capture([offer("p1", price="9,00", digest="new")], now=1000)
            pending = queue.pending(now=1000)
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["price"], "9,00")
            self.assertEqual(queue.revision_digests(now=1000), {("-123", 7): "new"})
            queue.close()

    def test_multiple_coupon_rows_from_same_message_survive_together(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = CapturedOfferQueue(Path(directory) / "db.sqlite3", now=lambda: 1000)
            first = offer("coupon:a")
            second = offer("coupon:b")
            first["kind"] = second["kind"] = "coupon_alert"
            queue.replace_capture([first, second], now=1000)
            self.assertEqual(
                {row["product_id"] for row in queue.pending(now=1000)},
                {"coupon:a", "coupon:b"},
            )
            queue.close()


    def test_expiry_is_bounded_by_source_date(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = Clock(1000)
            queue = CapturedOfferQueue(Path(directory) / "db.sqlite3", now=clock)
            queue.replace_capture(
                [offer("p1", stamp=1000)],
                max_age_minutes=2,
                recovery_max_age_minutes=1,
                now=1000,
            )
            self.assertEqual(len(queue.pending(now=1119)), 1)
            self.assertEqual(queue.pending(now=1121), [])
            queue.close()

    def test_recovered_offer_uses_shorter_ttl(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = CapturedOfferQueue(Path(directory) / "db.sqlite3", now=lambda: 1000)
            queue.replace_capture(
                [offer("p1", recovered=True, stamp=1000)],
                max_age_minutes=120,
                recovery_max_age_minutes=45,
                now=1000,
            )
            self.assertEqual(len(queue.pending(now=3699)), 1)
            self.assertEqual(queue.pending(now=3701), [])
            queue.close()

    def test_legacy_import_keeps_latest_revision_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            path = base / "fila_ofertas_v2.jsonl"
            rows = [
                offer("p1", price="10,00", digest="old"),
                offer("p1", price="9,00", digest="new"),
                offer("p2", message=8, digest="x"),
            ]
            path.write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n",
                encoding="utf-8",
            )
            queue = CapturedOfferQueue(base / "db.sqlite3", now=lambda: 1000)
            first = queue.import_legacy_jsonl(path, now=1000)
            self.assertEqual(first["scanned"], 3)
            pending = {row["product_id"]: row for row in queue.pending(now=1000)}
            self.assertEqual(pending["p1"]["price"], "9,00")
            second = queue.import_legacy_jsonl(path, now=1000)
            self.assertTrue(second["skipped"])
            self.assertEqual(len(queue.pending(now=1000)), 2)
            queue.close()

    def test_discard_product_removes_sent_offer(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = CapturedOfferQueue(Path(directory) / "db.sqlite3", now=lambda: 1000)
            queue.replace_capture([offer("p1")], now=1000)
            self.assertEqual(queue.discard_product("p1"), 1)
            self.assertEqual(queue.pending(now=1000), [])
            queue.close()


if __name__ == "__main__":
    unittest.main()
