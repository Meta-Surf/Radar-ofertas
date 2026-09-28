import json
from pathlib import Path
import shutil
import tempfile
import unittest
from banner_asset import restore_banner

BASE = Path(__file__).resolve().parents[1]

class BannerAssetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        shutil.copytree(BASE / 'assets/banner_cupons.parts', self.base / 'assets/banner_cupons.parts')

    def test_reconstruct_original_and_repeat(self):
        import hashlib
        target = restore_banner(self.base)
        manifest = json.loads((self.base / 'assets/banner_cupons.parts/manifest.json').read_text())
        self.assertTrue(target.read_bytes().startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), manifest['sha256'])
        before = target.stat().st_mtime_ns
        self.assertEqual(restore_banner(self.base), target)
        self.assertEqual(target.stat().st_mtime_ns, before)

    def test_corrupt_part_does_not_replace_existing(self):
        target = self.base / 'assets/banner_cupons.png'
        target.write_bytes(b'existing')
        (self.base / 'assets/banner_cupons.parts/000.b64').write_text('AAAA')
        with self.assertRaises(ValueError): restore_banner(self.base)
        self.assertEqual(target.read_bytes(), b'existing')

    def test_coupon_fallback_restores_missing_file(self):
        from cupons_shopee import banner_path
        from unittest.mock import patch
        with patch.dict('os.environ', {'CUPONS_BANNER': ''}):
            self.assertEqual(banner_path(self.base), self.base / 'assets/banner_cupons.png')
