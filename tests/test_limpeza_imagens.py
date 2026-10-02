import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from limpeza_imagens import ImageJanitor


class ImageJanitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.media = self.base / "imagens_ofertas"
        self.media.mkdir()
        self.db_path = self.base / "publicacoes.sqlite3"
        db = sqlite3.connect(self.db_path)
        db.execute("CREATE TABLE captured_queue(payload TEXT NOT NULL)")
        db.execute("CREATE TABLE radar_queue(payload TEXT NOT NULL)")
        db.commit()
        db.close()

    def _file(self, name, *, mtime=1000):
        path = self.media / name
        path.write_bytes(b"image")
        os.utime(path, (mtime, mtime))
        return path

    def test_removes_only_old_unreferenced_files(self):
        referenced = self._file("referenced.jpg")
        old = self._file("old.jpg")
        recent = self._file("recent.jpg", mtime=9500)
        db = sqlite3.connect(self.db_path)
        db.execute(
            "INSERT INTO captured_queue(payload) VALUES (?)",
            (json.dumps({"image": "imagens_ofertas/referenced.jpg"}),),
        )
        db.commit()
        db.close()

        janitor = ImageJanitor(
            self.media, self.db_path, retention_seconds=3600,
            now=lambda: 10000, monotonic=lambda: 10,
        )
        result = janitor.cleanup(force=True)
        self.assertEqual(result["removed"], 1)
        self.assertTrue(referenced.exists())
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())

    def test_interval_avoids_repeated_scans(self):
        ticks = iter([10, 20, 80])
        janitor = ImageJanitor(
            self.media, self.db_path, retention_seconds=3600,
            interval_seconds=60, now=lambda: 10000, monotonic=lambda: next(ticks),
        )
        self.assertTrue(janitor.cleanup(force=True)["ran"])
        self.assertFalse(janitor.cleanup()["ran"])
        self.assertTrue(janitor.cleanup()["ran"])


if __name__ == "__main__":
    unittest.main()
