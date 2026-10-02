import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import imagem_marca


class ImagemMarcaTests(unittest.TestCase):
    def test_configured_chats(self):
        with patch.dict(os.environ, {"TG_REBRAND_CHATS": "-1001, -1002"}, clear=False):
            self.assertEqual(imagem_marca.configured_chats(), {"-1001", "-1002"})
            self.assertTrue(imagem_marca.enabled(-1001))
            self.assertFalse(imagem_marca.enabled(-1003))

    def test_apply_covers_top_and_price_badge_but_preserves_center(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "oferta.jpg"
            Image.new("RGB", (1000, 1000), (200, 200, 200)).save(path)

            with patch.dict(os.environ, {"TELEGRAM_CANAL": "@ofertasbrasil_shopee"}, clear=False):
                imagem_marca.apply(path)

            with Image.open(path) as result:
                self.assertEqual(result.size, (1000, 1000))
                self.assertLess(sum(result.getpixel((500, 20))), 300)
                center = result.getpixel((500, 500))
                self.assertTrue(all(185 <= value <= 215 for value in center))
                self.assertLess(sum(result.getpixel((800, 850))), 350)

    def test_small_image_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tiny.jpg"
            Image.new("RGB", (80, 80), (200, 200, 200)).save(path)
            with self.assertRaises(ValueError):
                imagem_marca.apply(path)

    def test_invalid_box_falls_back_to_default(self):
        with patch.dict(os.environ, {"TG_REBRAND_PRICE_BOX": "1,0,0,1"}, clear=False):
            self.assertEqual(imagem_marca._price_box(), (0.64, 0.77, 0.98, 0.95))


if __name__ == "__main__":
    unittest.main()
