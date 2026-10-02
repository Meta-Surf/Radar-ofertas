"""Métricas leves de latência dos caminhos críticos do Radar."""
import sqlite3
import time
from pathlib import Path


class RuntimeMetrics:
    def __init__(self, path_or_db, *, now=time.time):
        self.now = now
        self._owns_db = not isinstance(path_or_db, sqlite3.Connection)
        if self._owns_db:
            self.path = Path(path_or_db)
            self.db = sqlite3.connect(self.path, timeout=10)
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA busy_timeout=5000")
        else:
            self.path = None
            self.db = path_or_db
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS runtime_metrics (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          component TEXT NOT NULL,
          operation TEXT NOT NULL,
          outcome TEXT NOT NULL,
          duration_ms REAL NOT NULL,
          created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS runtime_metrics_lookup
          ON runtime_metrics(component,operation,created_at);
        """)
        self.db.commit()
        self.prune()

    def record(self, component, operation, duration_ms, outcome="ok"):
        duration = max(0.0, min(float(duration_ms), 3_600_000.0))
        try:
            with self.db:
                self.db.execute(
                    """INSERT INTO runtime_metrics
                       (component,operation,outcome,duration_ms,created_at)
                       VALUES (?,?,?,?,?)""",
                    (
                        str(component or "unknown")[:40],
                        str(operation or "unknown")[:60],
                        str(outcome or "unknown")[:40],
                        duration,
                        self.now(),
                    ),
                )
            return True
        except sqlite3.Error:
            return False

    def prune(self, days=7):
        cutoff = self.now() - max(1, int(days)) * 86400
        try:
            with self.db:
                return self.db.execute(
                    "DELETE FROM runtime_metrics WHERE created_at<?", (cutoff,)
                ).rowcount
        except sqlite3.Error:
            return 0
    def summary(self, hours=24):
        cutoff = self.now() - max(1, int(hours)) * 3600
        rows = self.db.execute(
            """SELECT component,operation,duration_ms
               FROM runtime_metrics WHERE created_at>=?
               ORDER BY component,operation,duration_ms""",
            (cutoff,),
        ).fetchall()
        grouped = {}
        for component, operation, duration in rows:
            grouped.setdefault((component, operation), []).append(float(duration))
        result = {}
        for key, values in grouped.items():
            count = len(values)
            p95_index = max(0, min(count - 1, int((count - 1) * 0.95)))
            result[key] = {
                "count": count,
                "avg_ms": round(sum(values) / count, 1),
                "p95_ms": round(values[p95_index], 1),
                "max_ms": round(values[-1], 1),
            }
        return result

    def close(self):
        if self._owns_db:
            self.db.close()
