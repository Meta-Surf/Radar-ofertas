"""Persistência aditiva para entregas em múltiplos destinos.

O Telegram continua usando o fluxo legado nesta fase. Esta tabela recebe
espelhamento de estados terminais e será a base da futura Outbox.
"""
import hashlib
import json
import re
import time

VALID_STATES = {"PENDING", "SENDING", "SENT", "RETRY", "UNCERTAIN", "FAILED", "SKIPPED"}


def normalize_destination(value):
    value = re.sub(r"[^a-z0-9_-]+", "-", str(value or "").strip().lower()).strip("-")
    if not value or len(value) > 40:
        raise ValueError("destino inválido")
    return value


def payload_revision(payload):
    raw = json.dumps(payload or {}, sort_keys=True, ensure_ascii=False, default=str,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
class DeliveryOutbox:
    """Schema multicanal compartilhado no publicacoes.sqlite3."""

    def __init__(self, db, *, now=time.time):
        self.db = db
        self.now = now
        self.ensure_schema()

    def ensure_schema(self):
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS deliveries (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          product TEXT NOT NULL,
          destination TEXT NOT NULL,
          destination_account TEXT NOT NULL DEFAULT '',
          surface TEXT NOT NULL DEFAULT 'default',
          day TEXT NOT NULL,
          revision TEXT NOT NULL DEFAULT '',
          state TEXT NOT NULL,
          external_id TEXT,
          attempts INTEGER NOT NULL DEFAULT 0,
          retry_at REAL,
          error_reason TEXT NOT NULL DEFAULT '',
          payload TEXT,
          created_at REAL NOT NULL,
          updated_at REAL NOT NULL,
          sending_at REAL,
          sent_at REAL,
          UNIQUE(product,destination,destination_account,surface,day)
        );
        CREATE INDEX IF NOT EXISTS deliveries_state_retry_idx
          ON deliveries(destination,state,retry_at);
        CREATE INDEX IF NOT EXISTS deliveries_product_idx
          ON deliveries(product,destination);
        """)

    def backfill_telegram_posts(self, account="", *, commit=True):
        """Migra histórico legado sem sobrescrever entregas já enriquecidas."""
        now = self.now()
        revision = payload_revision({})
        inserted = 0
        try:
            rows = self.db.execute(
                "SELECT product,day,status,message_id,COALESCE(updated_at,0) "
                "FROM posts WHERE status IN ('sent','uncertain')"
            ).fetchall()
        except Exception:
            return 0
        for product, day, state, message_id, updated_at in rows:
            sent = state == "sent" and message_id is not None
            if not sent and state != "uncertain":
                continue
            stamp = float(updated_at or now)
            result = self.db.execute("""
                INSERT OR IGNORE INTO deliveries
                  (product,destination,destination_account,surface,day,revision,state,
                   external_id,attempts,retry_at,error_reason,payload,created_at,
                   updated_at,sending_at,sent_at)
                VALUES (?,?,?,?,?,?,?, ?,1,NULL,?,?,?, ?,NULL,?)
            """, (
                str(product), "telegram", str(account or ""), "channel", str(day),
                revision, "SENT" if sent else "UNCERTAIN",
                str(message_id) if sent else None,
                "" if sent else "LEGACY_ENVIO_INCERTO", "{}", stamp, stamp,
                stamp if sent else None,
            ))
            inserted += int(result.rowcount or 0)
        if commit:
            self.db.commit()
        return inserted

    def enqueue(self, product, *, destination, account="", surface="default",
                day, payload, commit=True):
        destination = normalize_destination(destination)
        now = self.now()
        raw = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"))
        revision = payload_revision(payload)
        self.db.execute("""
            INSERT INTO deliveries
              (product,destination,destination_account,surface,day,revision,state,
               attempts,error_reason,payload,created_at,updated_at)
            VALUES (?,?,?,?,?,?,'PENDING',0,'',?,?,?)
            ON CONFLICT(product,destination,destination_account,surface,day)
            DO UPDATE SET revision=excluded.revision,payload=excluded.payload,
              updated_at=excluded.updated_at
            WHERE deliveries.state IN ('PENDING','RETRY','FAILED','SKIPPED')
        """, (str(product), destination, str(account or ""), str(surface or "default"),
              str(day), revision, raw, now, now))
        if commit:
            self.db.commit()
        return revision

    def ready(self, destination, *, limit=50):
        destination = normalize_destination(destination)
        now = self.now()
        rows = self.db.execute("""
            SELECT id,product,destination_account,surface,day,revision,payload,attempts
            FROM deliveries WHERE destination=? AND state IN ('PENDING','RETRY')
              AND (retry_at IS NULL OR retry_at<=?)
            ORDER BY created_at,id LIMIT ?
        """, (destination, now, max(1, min(int(limit), 500)))).fetchall()
        return rows

    def claim(self, delivery_id, *, commit=True):
        now = self.now()
        result = self.db.execute("""
            UPDATE deliveries SET state='SENDING',attempts=attempts+1,
              sending_at=?,updated_at=? WHERE id=? AND state IN ('PENDING','RETRY')
              AND (retry_at IS NULL OR retry_at<=?)
        """, (now, now, int(delivery_id), now))
        if commit:
            self.db.commit()
        return bool(result.rowcount)

    def record_sent(self, product, *, destination, account="", surface="default",
                    day, external_id, payload=None, commit=True):
        destination = normalize_destination(destination)
        now = self.now()
        raw = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"))
        revision = payload_revision(payload)
        self.db.execute("""
            INSERT INTO deliveries
              (product,destination,destination_account,surface,day,revision,state,
               external_id,attempts,retry_at,error_reason,payload,created_at,
               updated_at,sending_at,sent_at)
            VALUES (?,?,?,?,?,?,'SENT',?,1,NULL,'',?,?,?,NULL,?)
            ON CONFLICT(product,destination,destination_account,surface,day)
            DO UPDATE SET revision=excluded.revision,state='SENT',
              external_id=excluded.external_id,attempts=MAX(deliveries.attempts,1),
              retry_at=NULL,error_reason='',payload=excluded.payload,
              updated_at=excluded.updated_at,sent_at=excluded.sent_at
        """, (str(product), destination, str(account or ""), str(surface or "default"),
              str(day), revision, str(external_id), raw, now, now, now))
        if commit:
            self.db.commit()
        return revision

    def record_uncertain(self, product, *, destination, account="", surface="default",
                         day, payload=None, reason="", commit=True):
        destination = normalize_destination(destination)
        now = self.now()
        raw = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"))
        revision = payload_revision(payload)
        self.db.execute("""
            INSERT INTO deliveries
              (product,destination,destination_account,surface,day,revision,state,
               external_id,attempts,retry_at,error_reason,payload,created_at,
               updated_at,sending_at,sent_at)
            VALUES (?,?,?,?,?,?,'UNCERTAIN',NULL,1,NULL,?,?,?, ?,?,NULL)
            ON CONFLICT(product,destination,destination_account,surface,day)
            DO UPDATE SET revision=excluded.revision,state='UNCERTAIN',
              attempts=MAX(deliveries.attempts,1),retry_at=NULL,
              error_reason=excluded.error_reason,payload=excluded.payload,
              updated_at=excluded.updated_at
            WHERE deliveries.state!='SENT'
        """, (str(product), destination, str(account or ""), str(surface or "default"),
              str(day), revision, str(reason or "")[:240], raw, now, now, now))
        if commit:
            self.db.commit()
        return revision

    def retry(self, delivery_id, *, delay, reason, commit=True):
        now = self.now()
        retry_at = now + max(1, int(delay))
        result = self.db.execute("""
            UPDATE deliveries SET state='RETRY',retry_at=?,error_reason=?,updated_at=?
            WHERE id=? AND state='SENDING'
        """, (retry_at, str(reason or "")[:240], now, int(delivery_id)))
        if commit:
            self.db.commit()
        return bool(result.rowcount)

    def fail(self, delivery_id, *, reason, commit=True):
        now = self.now()
        result = self.db.execute("""
            UPDATE deliveries SET state='FAILED',retry_at=NULL,error_reason=?,updated_at=?
            WHERE id=? AND state IN ('PENDING','SENDING','RETRY')
        """, (str(reason or "")[:240], now, int(delivery_id)))
        if commit:
            self.db.commit()
        return bool(result.rowcount)

    def counts(self):
        return {
            (destination, state): int(count)
            for destination, state, count in self.db.execute(
                "SELECT destination,state,COUNT(*) FROM deliveries "
                "GROUP BY destination,state ORDER BY destination,state"
            )
        }
