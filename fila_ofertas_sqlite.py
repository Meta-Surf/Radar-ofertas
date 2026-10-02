"""Fila SQLite para ofertas captadas dos grupos Telegram."""
import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path


def _source_timestamp(offer):
    try:
        value = str(offer.get("source_date") or "")
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.timestamp()
    except (TypeError, ValueError):
        return 0.0


def _queue_key(offer):
    product = str(offer.get("product_id") or "").strip()
    chat_id = offer.get("chat_id")
    message_id = offer.get("message_id")
    if chat_id is not None and isinstance(message_id, int) and product:
        return f"tg:{chat_id}:{message_id}:{product}"
    if product:
        return "product:" + product
    raw = json.dumps(offer, sort_keys=True, ensure_ascii=False, default=str)
    return "hash:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


class CapturedOfferQueue:
    def __init__(self, path, *, now=time.time):
        self.path = Path(path)
        self.now = now
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS captured_queue (
              queue_key TEXT PRIMARY KEY,
              product TEXT NOT NULL,
              chat_id TEXT,
              message_id INTEGER,
              payload TEXT NOT NULL,
              capture_digest TEXT,
              source_date TEXT,
              recovered INTEGER NOT NULL DEFAULT 0,
              enqueued_at REAL NOT NULL,
              updated_at REAL NOT NULL,
              expires_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS captured_queue_expiry_idx
              ON captured_queue(expires_at);
            CREATE INDEX IF NOT EXISTS captured_queue_product_idx
              ON captured_queue(product);
            CREATE INDEX IF NOT EXISTS captured_queue_source_idx
              ON captured_queue(chat_id,message_id);
            CREATE TABLE IF NOT EXISTS queue_migrations (
              name TEXT PRIMARY KEY,
              fingerprint TEXT NOT NULL,
              migrated_at REAL NOT NULL
            );
            """
        )
        self.db.commit()


    def close(self):
        self.db.close()

    def _expires_at(self, offer, max_age_minutes, recovery_max_age_minutes, now):
        age_minutes = recovery_max_age_minutes if offer.get("recovered") else max_age_minutes
        age_minutes = max(1, int(age_minutes))
        source = _source_timestamp(offer)
        if source <= 0:
            return now + age_minutes * 60
        return source + age_minutes * 60

    def replace_capture(
        self,
        offers,
        *,
        max_age_minutes=120,
        recovery_max_age_minutes=45,
        now=None,
    ):
        offers = [dict(o) for o in offers if isinstance(o, dict)]
        if not offers:
            return 0
        now = self.now() if now is None else float(now)
        groups = {}
        loose = []
        for offer in offers:
            chat_id = offer.get("chat_id")
            message_id = offer.get("message_id")
            if chat_id is not None and isinstance(message_id, int):
                groups.setdefault((str(chat_id), message_id), []).append(offer)
            else:
                loose.append(offer)

        inserted = 0
        with self.db:
            for (chat_id, message_id), batch in groups.items():
                self.db.execute(
                    "DELETE FROM captured_queue WHERE chat_id=? AND message_id=?",
                    (chat_id, message_id),
                )
                for offer in batch:
                    inserted += self._upsert(
                        offer, now, max_age_minutes, recovery_max_age_minutes
                    )
            for offer in loose:
                inserted += self._upsert(
                    offer, now, max_age_minutes, recovery_max_age_minutes
                )
            self.db.execute("DELETE FROM captured_queue WHERE expires_at<=?", (now,))
        return inserted

    def _upsert(self, offer, now, max_age_minutes, recovery_max_age_minutes):
        product = str(offer.get("product_id") or "").strip()
        if not product:
            return 0
        expires_at = self._expires_at(
            offer, max_age_minutes, recovery_max_age_minutes, now
        )
        if expires_at <= now:
            return 0
        payload = json.dumps(offer, ensure_ascii=False, separators=(",", ":"))
        self.db.execute(
            """INSERT INTO captured_queue
               (queue_key,product,chat_id,message_id,payload,capture_digest,
                source_date,recovered,enqueued_at,updated_at,expires_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(queue_key) DO UPDATE SET
                 product=excluded.product,
                 chat_id=excluded.chat_id,
                 message_id=excluded.message_id,
                 payload=excluded.payload,
                 capture_digest=excluded.capture_digest,
                 source_date=excluded.source_date,
                 recovered=excluded.recovered,
                 updated_at=excluded.updated_at,
                 expires_at=excluded.expires_at""",
            (
                _queue_key(offer),
                product,
                str(offer.get("chat_id")) if offer.get("chat_id") is not None else None,
                offer.get("message_id") if isinstance(offer.get("message_id"), int) else None,
                payload,
                str(offer.get("capture_digest") or "") or None,
                str(offer.get("source_date") or "") or None,
                1 if offer.get("recovered") else 0,
                now,
                now,
                expires_at,
            ),
        )
        return 1


    def prune(self, now=None):
        now = self.now() if now is None else float(now)
        with self.db:
            return self.db.execute(
                "DELETE FROM captured_queue WHERE expires_at<=?", (now,)
            ).rowcount

    def pending(self, now=None):
        now = self.now() if now is None else float(now)
        self.prune(now)
        rows = self.db.execute(
            """SELECT payload FROM captured_queue
               WHERE expires_at>?
               ORDER BY COALESCE(source_date,''), updated_at""",
            (now,),
        ).fetchall()
        out = []
        for payload, in rows:
            try:
                value = json.loads(payload)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                out.append(value)
        return out

    def revision_digests(self, now=None):
        now = self.now() if now is None else float(now)
        self.prune(now)
        result = {}
        for chat_id, message_id, digest in self.db.execute(
            """SELECT chat_id,message_id,capture_digest
               FROM captured_queue
               WHERE expires_at>? AND chat_id IS NOT NULL
                 AND message_id IS NOT NULL AND capture_digest IS NOT NULL
               ORDER BY updated_at""",
            (now,),
        ):
            result[(str(chat_id), int(message_id))] = str(digest)
        return result

    def discard_product(self, product):
        with self.db:
            return self.db.execute(
                "DELETE FROM captured_queue WHERE product=?", (str(product),)
            ).rowcount

    def discard_source(self, chat_id, message_id):
        with self.db:
            return self.db.execute(
                "DELETE FROM captured_queue WHERE chat_id=? AND message_id=?",
                (str(chat_id), int(message_id)),
            ).rowcount

    def stats(self, now=None):
        now = self.now() if now is None else float(now)
        self.prune(now)
        total = self.db.execute(
            "SELECT COUNT(*) FROM captured_queue WHERE expires_at>?", (now,)
        ).fetchone()[0]
        sources = {}
        for payload, in self.db.execute(
            "SELECT payload FROM captured_queue WHERE expires_at>?", (now,)
        ):
            try:
                offer = json.loads(payload)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            label = str(offer.get("store") or offer.get("source") or "unknown")
            sources[label] = sources.get(label, 0) + 1
        return {"total": int(total), "sources": sources}


    def import_legacy_jsonl(
        self,
        path,
        *,
        max_age_minutes=120,
        recovery_max_age_minutes=45,
        now=None,
    ):
        path = Path(path)
        if not path.is_file():
            return {"scanned": 0, "imported": 0, "skipped": True}
        stat = path.stat()
        fingerprint = f"{stat.st_size}:{stat.st_mtime_ns}"
        marker = "jsonl:" + str(path.resolve())
        previous = self.db.execute(
            "SELECT fingerprint FROM queue_migrations WHERE name=?", (marker,)
        ).fetchone()
        if previous and previous[0] == fingerprint:
            return {"scanned": 0, "imported": 0, "skipped": True}

        scanned = 0
        grouped = {}
        loose = {}
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return {"scanned": 0, "imported": 0, "skipped": True}
        for line in lines:
            try:
                offer = json.loads(line)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(offer, dict) or not offer.get("product_id"):
                continue
            scanned += 1
            chat_id = offer.get("chat_id")
            message_id = offer.get("message_id")
            if chat_id is not None and isinstance(message_id, int):
                source = (str(chat_id), message_id)
                digest = str(offer.get("capture_digest") or "")
                current = grouped.get(source)
                if current is None or current["digest"] != digest:
                    grouped[source] = {
                        "digest": digest,
                        "offers": {str(offer["product_id"]): offer},
                    }
                else:
                    current["offers"][str(offer["product_id"])] = offer
            else:
                loose[str(offer["product_id"])] = offer

        now = self.now() if now is None else float(now)
        imported = 0
        for item in grouped.values():
            imported += self.replace_capture(
                list(item["offers"].values()),
                max_age_minutes=max_age_minutes,
                recovery_max_age_minutes=recovery_max_age_minutes,
                now=now,
            )
        for offer in loose.values():
            imported += self.replace_capture(
                [offer],
                max_age_minutes=max_age_minutes,
                recovery_max_age_minutes=recovery_max_age_minutes,
                now=now,
            )
        with self.db:
            self.db.execute(
                """INSERT INTO queue_migrations(name,fingerprint,migrated_at)
                   VALUES (?,?,?)
                   ON CONFLICT(name) DO UPDATE SET
                     fingerprint=excluded.fingerprint,
                     migrated_at=excluded.migrated_at""",
                (marker, fingerprint, now),
            )
        return {"scanned": scanned, "imported": imported, "skipped": False}
