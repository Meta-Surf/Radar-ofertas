"""Backoff persistente por oferta para o publicador unificado."""
import hashlib
import json
import time
from revisao_publicacao import immediate, matches, retry_key, current_row, digest


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

    def failure(self, retry_key, revision, reason, *, base=60, cap=1800, selected=None):
        if selected is not None and self.enabled:
            with immediate(self.db):
                if not matches(self.db, selected, now=self.now()):
                    return {'failures': 0, 'delay': 0, 'next_at': self.now(), 'stale': True}
                return self._failure(retry_key, revision, reason, base=base, cap=cap)
        return self._failure(retry_key, revision, reason, base=base, cap=cap)

    def _failure(self, retry_key, revision, reason, *, base=60, cap=1800):
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
        with immediate(self.db):
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


    def clear(self, retry_key, revision=None):
        if not self.enabled:
            return
        with self.db:
            self.db.execute(
                "DELETE FROM publisher_retry WHERE retry_key=? AND (? IS NULL OR revision=?)",
                (retry_key, revision, revision),
            )

    def for_selection(self, selected):
        return SelectedBackoff(self, selected) if selected else self

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


class SelectedBackoff:
    """Uma origem/revisão nunca limpa ou adia o retry de outra origem/revisão."""
    def __init__(self, parent, selected):
        self.parent, self.selected = parent, selected
        self.key, self.revision = retry_key(selected), selected['digest']
        row = current_row(parent.db, selected)
        original = json.loads(row[0]) if row and digest(row[0]) == self.revision else {}
        self.legacy_key = original.get('product_id')
        self.legacy_revision = offer_revision(original)

    def remaining(self, _key, _revision):
        if not self.parent.enabled:
            return 0
        row = self.parent.db.execute('SELECT revision,next_at FROM publisher_retry WHERE retry_key=?',
                                     (self.key,)).fetchone()
        remaining = max(0, int(row[1] - self.parent.now() + .999)) if row and row[0] == self.revision else 0
        legacy = self.parent.db.execute('SELECT revision,next_at FROM publisher_retry WHERE retry_key=?',
                                        (self.legacy_key,)).fetchone()
        if legacy and legacy[0] == self.legacy_revision:
            remaining = max(remaining, max(0, int(legacy[1] - self.parent.now() + .999)))
        return remaining

    def failure(self, _key, _revision, reason, **kwargs):
        return self.parent.failure(self.key, self.revision, reason, selected=self.selected, **kwargs)

    def clear(self, _key):
        self.parent.clear(self.key, self.revision)
        if self.legacy_key:
            self.parent.clear(self.legacy_key, self.legacy_revision)
