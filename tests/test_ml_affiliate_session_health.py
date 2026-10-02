import sqlite3
import unittest

from mercadolivre_session_health import (
    MLAffiliateSessionHealth,
    credential_fingerprint,
)


class Clock:
    def __init__(self, value=1000):
        self.value = float(value)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class MLAffiliateSessionHealthTests(unittest.TestCase):
    def test_failure_opens_circuit_and_alerts_once(self):
        db = sqlite3.connect(":memory:")
        clock = Clock()
        alerts = []
        health = MLAffiliateSessionHealth(
            db, now=clock,
            notifier=lambda message: alerts.append(message) or True,
        )
        fp = credential_fingerprint("cookie", "csrf", "tag")
        state = health.failure(fp, 403, "Sessão recusada")
        self.assertEqual(state["retry_after"], 900)
        self.assertTrue(state["alert_sent"])
        can_try, current = health.can_try(fp)
        self.assertFalse(can_try)
        self.assertEqual(current["last_status"], 403)
        self.assertEqual(len(alerts), 1)

        clock.advance(901)
        state = health.failure(fp, 403, "Sessão recusada")
        self.assertEqual(state["retry_after"], 3600)
        self.assertEqual(len(alerts), 1)
        db.close()


    def test_changed_credentials_bypass_old_circuit(self):
        db = sqlite3.connect(":memory:")
        clock = Clock()
        health = MLAffiliateSessionHealth(
            db, now=clock, notifier=lambda message: True
        )
        old = credential_fingerprint("cookie-old", "csrf", "tag")
        new = credential_fingerprint("cookie-new", "csrf", "tag")
        health.failure(old, 401, "expired")
        can_try, state = health.can_try(new)
        self.assertTrue(can_try)
        self.assertEqual(state["state"], "unknown")
        self.assertEqual(health.status()["failures"], 0)
        db.close()

    def test_success_closes_circuit_and_sends_recovery(self):
        db = sqlite3.connect(":memory:")
        clock = Clock()
        alerts = []
        health = MLAffiliateSessionHealth(
            db, now=clock,
            notifier=lambda message: alerts.append(message) or True,
        )
        fp = credential_fingerprint("cookie", "csrf", "tag")
        health.failure(fp, 403, "expired")
        clock.advance(901)
        recovered = health.success(fp)
        self.assertTrue(recovered)
        self.assertEqual(health.status()["state"], "healthy")
        self.assertEqual(health.status()["retry_after"], 0)
        self.assertEqual(len(alerts), 2)
        self.assertIn("voltou a funcionar", alerts[-1])
        db.close()

    def test_fingerprint_is_stable_and_non_secret(self):
        first = credential_fingerprint("secret-cookie", "secret-csrf", "tag")
        second = credential_fingerprint("secret-cookie", "secret-csrf", "tag")
        self.assertEqual(first, second)
        self.assertNotIn("secret", first)
        self.assertEqual(len(first), 64)


if __name__ == "__main__":
    unittest.main()
