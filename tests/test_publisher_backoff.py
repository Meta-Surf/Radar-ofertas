import sqlite3
import tempfile
import unittest
from pathlib import Path

from publisher_backoff import PublisherBackoff, offer_revision


class Clock:
    def __init__(self, value=1000):
        self.value = float(value)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class PublisherBackoffTests(unittest.TestCase):
    def test_progressive_delay_is_bounded(self):
        db = sqlite3.connect(":memory:")
        clock = Clock()
        backoff = PublisherBackoff(db, now=clock)
        delays = [
            backoff.failure("p", "r", "SEM_IMAGEM", base=60)["delay"]
            for _ in range(6)
        ]
        self.assertEqual(delays, [60, 180, 540, 1620, 1800, 1800])
        db.close()

    def test_remaining_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "db.sqlite3"
            clock = Clock()
            db = sqlite3.connect(path)
            PublisherBackoff(db, now=clock).failure(
                "p", "r", "API", base=300
            )
            db.close()

            clock.advance(120)
            db = sqlite3.connect(path)
            restored = PublisherBackoff(db, now=clock)
            self.assertEqual(restored.remaining("p", "r"), 180)
            state = restored.failure("p", "r", "API", base=300)
            self.assertEqual(state["failures"], 2)
            self.assertEqual(state["delay"], 900)
            db.close()

    def test_revision_change_resets_old_backoff(self):
        db = sqlite3.connect(":memory:")
        clock = Clock()
        backoff = PublisherBackoff(db, now=clock)
        backoff.failure("p", "old", "API", base=60)
        self.assertGreater(backoff.remaining("p", "old"), 0)
        self.assertEqual(backoff.remaining("p", "new"), 0)
        state = backoff.failure("p", "new", "API", base=60)
        self.assertEqual(state["failures"], 1)
        db.close()

    def test_clear_removes_retry(self):
        db = sqlite3.connect(":memory:")
        clock = Clock()
        backoff = PublisherBackoff(db, now=clock)
        backoff.failure("p", "r", "API", base=60)
        backoff.clear("p")
        self.assertEqual(backoff.remaining("p", "r"), 0)
        db.close()

    def test_disabled_mode_does_not_write_schema(self):
        db = sqlite3.connect(":memory:")
        clock = Clock()
        backoff = PublisherBackoff(db, now=clock, enabled=False)
        state = backoff.failure("p", "r", "API", base=60)
        self.assertEqual(state["delay"], 60)
        tables = {
            row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertNotIn("publisher_retry", tables)
        db.close()

    def test_offer_revision_changes_with_source_edit(self):
        first = offer_revision({"product_id": "x", "price": "10,00"})
        second = offer_revision({"product_id": "x", "price": "9,00"})
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
