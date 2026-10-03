"""Resiliência persistente para leitura pública do Mercado Livre."""
import re
import time
from revisao_publicacao import immediate, matches


WINDOW_403 = 5 * 60
CIRCUIT_SECONDS = 15 * 60
STALE_SECONDS = 14 * 86400


def classify(error):
    text = str(error or "").lower()
    if "403" in text:
        return "ML_HTTP_403"
    if "circular" in text or "limite de redirecionamentos" in text:
        return "ML_REDIRECT_LOOP"
    if "429" in text:
        return "ML_HTTP_429"
    if "timeout" in text or "tempo" in text:
        return "ML_TIMEOUT"
    return "ML_TRANSIENT"


def retry_delay(reason, failures):
    failures = max(1, int(failures))
    if reason == "ML_HTTP_403":
        steps = (300, 900, 3600, 21600)
    elif reason == "ML_REDIRECT_LOOP":
        steps = (900, 3600, 21600)
    elif reason == "ML_HTTP_429":
        steps = (900, 1800, 3600, 21600)
    else:
        steps = (300, 900, 1800, 3600)
    return steps[min(failures - 1, len(steps) - 1)]


def quarantine_seconds(reason, failures):
    if reason == "ML_HTTP_403" and failures >= 4:
        return 24 * 3600
    if reason == "ML_REDIRECT_LOOP" and failures >= 3:
        return 24 * 3600
    if reason == "ML_HTTP_429" and failures >= 4:
        return 12 * 3600
    if failures >= 6:
        return 6 * 3600
    return 0


class MLResilience:
    def __init__(self, db):
        self.db = db
        self._init_db()
    def _init_db(self):
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS ml_resolution_failures (
          product TEXT PRIMARY KEY,
          failures INTEGER NOT NULL,
          first_failure REAL NOT NULL,
          last_failure REAL NOT NULL,
          next_retry REAL NOT NULL,
          quarantine_until REAL NOT NULL DEFAULT 0,
          reason TEXT NOT NULL,
          details TEXT
        );
        CREATE INDEX IF NOT EXISTS ml_failure_reason_time
          ON ml_resolution_failures(reason,last_failure);
        CREATE TABLE IF NOT EXISTS ml_resolution_circuit (
          id INTEGER PRIMARY KEY CHECK(id=1),
          open_until REAL NOT NULL,
          reason TEXT NOT NULL,
          opened REAL NOT NULL
        );
        """)
        cutoff = time.time() - STALE_SECONDS
        with self.db:
            self.db.execute(
                "DELETE FROM ml_resolution_failures WHERE last_failure<?",
                (cutoff,),
            )

    def circuit(self, now=None):
        now = time.time() if now is None else float(now)
        row = self.db.execute(
            "SELECT open_until,reason FROM ml_resolution_circuit WHERE id=1"
        ).fetchone()
        if not row or row[0] <= now:
            return None
        return {
            "reason": "ML_CIRCUIT_BREAKER",
            "source_reason": row[1],
            "retry_after": max(1, int(row[0] - now)),
        }

    def can_try(self, product, now=None):
        now = time.time() if now is None else float(now)
        circuit = self.circuit(now)
        if circuit:
            return False, circuit
        row = self.db.execute(
            """SELECT failures,next_retry,quarantine_until,reason
               FROM ml_resolution_failures WHERE product=?""",
            (str(product),),
        ).fetchone()
        if not row:
            return True, None
        failures, next_retry, quarantine_until, reason = row
        blocked_until = max(float(next_retry), float(quarantine_until))
        if blocked_until <= now:
            return True, None
        return False, {
            "reason": "ML_QUARENTENA" if quarantine_until > now else "ML_BACKOFF",
            "source_reason": reason,
            "failures": failures,
            "retry_after": max(1, int(blocked_until - now)),
        }
    def _maybe_open_circuit(self, now):
        count = self.db.execute(
            """SELECT COUNT(*) FROM ml_resolution_failures
               WHERE reason='ML_HTTP_403' AND last_failure>=?""",
            (now - WINDOW_403,),
        ).fetchone()[0]
        if count < 5:
            return False
        with immediate(self.db):
            self.db.execute(
                """INSERT INTO ml_resolution_circuit(id,open_until,reason,opened)
                   VALUES(1,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     open_until=MAX(open_until,excluded.open_until),
                     reason=excluded.reason,opened=excluded.opened""",
                (now + CIRCUIT_SECONDS, "ML_HTTP_403", now),
            )
        return True

    def failure(self, product, error, now=None, *, selected=None):
        with immediate(self.db):
            if selected is not None and not matches(self.db, selected):
                return {'reason': 'REVISAO_SUBSTITUIDA', 'failures': 0,
                        'retry_after': 0, 'quarantined': False, 'circuit_opened': False}
            return self._failure(product, error, now)

    def _failure(self, product, error, now=None):
        now = time.time() if now is None else float(now)
        product = str(product)
        reason = classify(error)
        row = self.db.execute(
            "SELECT failures,first_failure FROM ml_resolution_failures WHERE product=?",
            (product,),
        ).fetchone()
        failures = (int(row[0]) if row else 0) + 1
        first = float(row[1]) if row else now
        delay = retry_delay(reason, failures)
        quarantine = quarantine_seconds(reason, failures)
        quarantine_until = now + quarantine if quarantine else 0
        details = re.sub(r"\s+", " ", str(error or "")).strip()[:240]
        with immediate(self.db):
            self.db.execute(
                """INSERT INTO ml_resolution_failures
                   (product,failures,first_failure,last_failure,next_retry,
                    quarantine_until,reason,details)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(product) DO UPDATE SET
                     failures=excluded.failures,
                     first_failure=excluded.first_failure,
                     last_failure=excluded.last_failure,
                     next_retry=excluded.next_retry,
                     quarantine_until=excluded.quarantine_until,
                     reason=excluded.reason,
                     details=excluded.details""",
                (
                    product, failures, first, now, now + delay,
                    quarantine_until, reason, details,
                ),
            )
        opened = self._maybe_open_circuit(now) if reason == "ML_HTTP_403" else False
        return {
            "reason": reason,
            "failures": failures,
            "retry_after": quarantine if quarantine else delay,
            "quarantined": bool(quarantine),
            "circuit_opened": opened,
        }

    def success(self, product, *, selected=None):
        with immediate(self.db):
            if selected is not None and not matches(self.db, selected):
                return
            self.db.execute(
                "DELETE FROM ml_resolution_failures WHERE product=?",
                (str(product),),
            )

    def stats(self, now=None):
        now = time.time() if now is None else float(now)
        rows = dict(self.db.execute(
            "SELECT reason,COUNT(*) FROM ml_resolution_failures GROUP BY reason"
        ))
        quarantined = self.db.execute(
            "SELECT COUNT(*) FROM ml_resolution_failures WHERE quarantine_until>?",
            (now,),
        ).fetchone()[0]
        return {
            "by_reason": rows,
            "quarantined": quarantined,
            "circuit": self.circuit(now),
        }
