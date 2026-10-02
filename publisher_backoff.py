"""Backoff persistente por oferta para o publicador unificado."""
import hashlib
import json
import time


def offer_revision(offer):
    raw = json.dumps(
        offer, sort_keys=True, ensure_ascii=False, default=str,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class PublisherBackoff:
    def __init__(self, db, *, now=time.time, enabled=True):
        self.db = db
        self.now = now
        self.enabled = bool(enabled)
        if not self.enabled:
            return
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS publisher_retry (
               retry_key TEXT PRIMARY KEY,
               revision TEXT NOT NULL,
               reason TEXT NOT NULL,
               failures INTEGER NOT NULL,
               next_at REAL NOT NULL,
               updated_at REAL NOT NULL)"""
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS publisher_retry_next_idx "
            "ON publisher_retry(next_at)"
        )
        self.db.commit()


    def remaining(self, retry_key, revision):
        if not self.enabled:
            return 0
        now = self.now()
        row = self.db.execute(
            """SELECT revision,next_at FROM publisher_retry
               WHERE retry_key=?""",
            (retry_key,),
        ).fetchone()
        if not row:
            return 0
        if row[0] != revision:
            self.clear(retry_key)
            return 0
        return max(0, int(row[1] - now + 0.999))

    def failure(self, retry_key, revision, reason, *, base=60, cap=1800):
        if not self.enabled:
            base = max(1, int(base))
            return {"failures": 1, "delay": base, "next_at": self.now() + base}
        now = self.now()
        row = self.db.execute(
            """SELECT revision,reason,failures FROM publisher_retry
               WHERE retry_key=?""",
            (retry_key,),
        ).fetchone()
        failures = 1
        if row and row[0] == revision and row[1] == reason:
            failures = int(row[2]) + 1
        base = max(1, int(base))
        cap = max(base, int(cap))
        delay = min(cap, base * (3 ** min(failures - 1, 6)))
        with self.db:
            self.db.execute(
                """INSERT INTO publisher_retry
                   (retry_key,revision,reason,failures,next_at,updated_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(retry_key) DO UPDATE SET
                   revision=excluded.revision,
                   reason=excluded.reason,
                   failures=excluded.failures,
                   next_at=excluded.next_at,
                   updated_at=excluded.updated_at""",
                (retry_key, revision, reason, failures, now + delay, now),
            )
        return {"failures": failures, "delay": delay, "next_at": now + delay}


    def clear(self, retry_key):
        if not self.enabled:
            return
        with self.db:
            self.db.execute(
                "DELETE FROM publisher_retry WHERE retry_key=?",
                (retry_key,),
            )

    def prune(self, max_age_seconds=604800):
        if not self.enabled:
            return 0
        cutoff = self.now() - max(3600, int(max_age_seconds))
        with self.db:
            return self.db.execute(
                "DELETE FROM publisher_retry WHERE updated_at<?",
                (cutoff,),
            ).rowcount

    def counts(self):
        if not self.enabled:
            return {}
        rows = self.db.execute(
            """SELECT reason,COUNT(*) FROM publisher_retry
               WHERE next_at>? GROUP BY reason ORDER BY COUNT(*) DESC""",
            (self.now(),),
        ).fetchall()
        return dict(rows)
