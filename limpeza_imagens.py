"""Limpeza conservadora de imagens temporárias já fora das filas ativas."""
import json
import sqlite3
import time
from pathlib import Path


class ImageJanitor:
    def __init__(self, media_dir, db_path, *, retention_seconds=86400,
                 interval_seconds=600, now=time.time, monotonic=time.monotonic):
        self.media_dir = Path(media_dir).resolve()
        self.db_path = Path(db_path)
        self.retention_seconds = max(3600, int(retention_seconds))
        self.interval_seconds = max(60, int(interval_seconds))
        self.now = now
        self.monotonic = monotonic
        self.last_run = None

    def _referenced(self):
        refs = set()
        try:
            db = sqlite3.connect(self.db_path, timeout=10)
            try:
                tables = {
                    row[0] for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                for table in ("captured_queue", "radar_queue"):
                    if table not in tables:
                        continue
                    for (raw,) in db.execute(f"SELECT payload FROM {table}"):
                        try:
                            payload = json.loads(raw)
                        except (TypeError, ValueError, json.JSONDecodeError):
                            continue
                        value = payload.get("image") if isinstance(payload, dict) else None
                        if not isinstance(value, str) or not value.strip():
                            continue
                        candidate = (self.media_dir.parent / value).resolve()
                        if candidate.is_relative_to(self.media_dir):
                            refs.add(candidate)
            finally:
                db.close()
        except sqlite3.Error:
            pass
        return refs
    def cleanup(self, *, force=False, max_delete=500):
        tick = self.monotonic()
        if (
            not force and self.last_run is not None
            and tick - self.last_run < self.interval_seconds
        ):
            return {"ran": False, "removed": 0, "kept": 0}
        self.last_run = tick

        self.media_dir.mkdir(parents=True, exist_ok=True)
        refs = self._referenced()
        cutoff = self.now() - self.retention_seconds
        removed = kept = 0
        for path in self.media_dir.rglob("*"):
            if removed >= max(1, int(max_delete)):
                break
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                resolved = path.resolve()
                if not resolved.is_relative_to(self.media_dir):
                    continue
                if resolved in refs or path.stat().st_mtime > cutoff:
                    kept += 1
                    continue
                path.unlink()
                removed += 1
            except OSError:
                kept += 1
        return {"ran": True, "removed": removed, "kept": kept}
