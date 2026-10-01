import sqlite3
import unittest

from mercadolivre_resiliencia import MLResilience, classify, retry_delay


class MLResilienceTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.r = MLResilience(self.db)

    def tearDown(self):
        self.db.close()

    def test_classifies_known_failures(self):
        self.assertEqual(classify("Consulta ML HTTP 403"), "ML_HTTP_403")
        self.assertEqual(classify("Limite de redirecionamentos ML atingido"), "ML_REDIRECT_LOOP")
        self.assertEqual(classify("HTTP 429"), "ML_HTTP_429")
        self.assertEqual(classify("timeout"), "ML_TIMEOUT")

    def test_backoff_grows(self):
        self.assertEqual(retry_delay("ML_HTTP_403", 1), 300)
        self.assertEqual(retry_delay("ML_HTTP_403", 2), 900)
        self.assertEqual(retry_delay("ML_HTTP_403", 3), 3600)

    def test_four_403_failures_quarantine_link(self):
        now = 1000
        for i in range(4):
            state = self.r.failure("ML:1", "HTTP 403", now=now + i)
        self.assertTrue(state["quarantined"])
        allowed, info = self.r.can_try("ML:1", now=now + 10)
        self.assertFalse(allowed)
        self.assertEqual(info["reason"], "ML_QUARENTENA")
        self.assertGreater(info["retry_after"], 23 * 3600)
    def test_three_redirect_loops_quarantine_link(self):
        for i in range(3):
            state = self.r.failure(
                "ML:loop", "Limite de redirecionamentos ML atingido", now=2000 + i
            )
        self.assertTrue(state["quarantined"])
        allowed, info = self.r.can_try("ML:loop", now=2010)
        self.assertFalse(allowed)
        self.assertEqual(info["source_reason"], "ML_REDIRECT_LOOP")

    def test_five_distinct_403_open_circuit(self):
        now = 3000
        for i in range(5):
            state = self.r.failure(f"ML:{i}", "HTTP 403", now=now + i)
        self.assertTrue(state["circuit_opened"])
        allowed, info = self.r.can_try("ML:nova", now=now + 10)
        self.assertFalse(allowed)
        self.assertEqual(info["reason"], "ML_CIRCUIT_BREAKER")
        self.assertEqual(info["source_reason"], "ML_HTTP_403")

    def test_success_clears_per_link_failure(self):
        self.r.failure("ML:ok", "HTTP 403", now=4000)
        self.r.success("ML:ok")
        allowed, info = self.r.can_try("ML:ok", now=4001)
        self.assertTrue(allowed)
        self.assertIsNone(info)

    def test_state_persists_in_database(self):
        import time
        now = time.time()
        self.r.failure("ML:persist", "HTTP 403", now=now)
        other = MLResilience(self.db)
        allowed, info = other.can_try("ML:persist", now=now + 1)
        self.assertFalse(allowed)
        self.assertEqual(info["source_reason"], "ML_HTTP_403")


if __name__ == "__main__":
    unittest.main()
