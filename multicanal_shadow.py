"""Fila sombra para validar novos destinos sem qualquer publicação externa."""
import json
import time

from distribuicao import normalize_destination, payload_revision
from instagram_adapter import InstagramDryRunAdapter


class ShadowDistribution:
    def __init__(self, db, *, now=time.time):
        self.db = db
        self.now = now
        self.ensure_schema()
        self.prune()

    def ensure_schema(self):
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS delivery_shadow (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          product TEXT NOT NULL,
          destination TEXT NOT NULL,
          surface TEXT NOT NULL,
          day TEXT NOT NULL,
          revision TEXT NOT NULL,
          state TEXT NOT NULL,
          reason TEXT NOT NULL DEFAULT '',
          source_external_id TEXT,
          payload TEXT NOT NULL,
          created_at REAL NOT NULL,
          updated_at REAL NOT NULL,
          UNIQUE(product,destination,surface,day)
        );
        CREATE INDEX IF NOT EXISTS delivery_shadow_state_idx
          ON delivery_shadow(destination,state,updated_at);
        """)
        self.db.commit()

    def prune(self, days=30):
        cutoff = self.now() - max(1, int(days)) * 86400
        try:
            with self.db:
                return self.db.execute(
                    "DELETE FROM delivery_shadow WHERE updated_at<?", (cutoff,)
                ).rowcount
        except Exception:
            return 0

    @staticmethod
    def adapter(destination):
        destination = normalize_destination(destination)
        if destination == "instagram":
            return InstagramDryRunAdapter()
        raise ValueError(f"destino shadow sem adaptador: {destination}")

    def record(self, offer, *, destination, day, source_external_id, image=None,
               commit=True):
        adapter = self.adapter(destination)
        prepared = adapter.prepare(dict(offer), image=image)
        destination = adapter.destination
        surface = adapter.surface
        state = str(prepared.get("state") or "BLOCKED")
        reasons = prepared.get("reasons") or []
        reason = ",".join(str(x) for x in reasons)[:240]
        revision = payload_revision(prepared)
        raw = json.dumps(prepared, ensure_ascii=False, separators=(",", ":"), default=str)
        now = self.now()
        self.db.execute("""
            INSERT INTO delivery_shadow
              (product,destination,surface,day,revision,state,reason,
               source_external_id,payload,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(product,destination,surface,day)
            DO UPDATE SET revision=excluded.revision,state=excluded.state,
              reason=excluded.reason,source_external_id=excluded.source_external_id,
              payload=excluded.payload,updated_at=excluded.updated_at
        """, (
            str(offer.get("product_id") or "UNKNOWN"),
            destination, surface, str(day), revision, state, reason,
            str(source_external_id), raw, now, now,
        ))
        if commit:
            self.db.commit()
        return prepared

    def mirror_success(self, offer, *, day, source_external_id, image=None,
                       destinations=()):
        results = {}
        for destination in destinations:
            destination = normalize_destination(destination)
            try:
                results[destination] = self.record(
                    offer,
                    destination=destination,
                    day=day,
                    source_external_id=source_external_id,
                    image=image,
                    commit=False,
                )
            except Exception as exc:
                results[destination] = {
                    "state": "BLOCKED",
                    "reasons": ["ADAPTER_ERROR:" + type(exc).__name__],
                    "publish_enabled": False,
                }
        self.db.commit()
        return results
    def counts(self):
        return {
            (destination, state): int(count)
            for destination, state, count in self.db.execute(
                """SELECT destination,state,COUNT(*) FROM delivery_shadow
                   GROUP BY destination,state ORDER BY destination,state"""
            )
        }

    def recent(self, destination, *, limit=20):
        destination = normalize_destination(destination)
        return self.db.execute(
            """SELECT product,state,reason,source_external_id,payload,updated_at
               FROM delivery_shadow WHERE destination=?
               ORDER BY updated_at DESC,id DESC LIMIT ?""",
            (destination, max(1, min(int(limit), 200))),
        ).fetchall()
