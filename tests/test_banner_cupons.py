import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import cupons_shopee as coupons

BASE = Path(__file__).resolve().parents[1]

class BannerTests(unittest.TestCase):
    def test_default_asset_and_empty_or_missing_override(self):
        for value in ['', '   ', 'arquivo_inexistente.png']:
            with self.subTest(value=value), patch.dict(os.environ, {'CUPONS_BANNER': value}):
                self.assertEqual(coupons.banner_path(BASE), BASE / 'assets/banner_cupons.png')

    def test_custom_image(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'custom.png'
            p.write_bytes(b'custom')
            with patch.dict(os.environ, {'CUPONS_BANNER': str(p)}):
                self.assertEqual(coupons.banner_path(BASE), p)

    def test_banner_upload_and_affiliate_link_in_text(self):
        alert = {'affiliate_generated': True, 'entries': [
            {'conditions': 'R$ 30 OFF acima de R$ 169',
             'affiliate_url': 'https://s.shopee.com.br/novo'}]}
        response = Mock()
        response.json.return_value = {'ok': True, 'result': {'message_id': 42}}
        def sent(url, **kwargs):
            self.assertTrue(url.endswith('/sendPhoto'))
            self.assertEqual(kwargs['files']['photo'][1].read(),
                             (BASE / 'assets/banner_cupons.png').read_bytes())
            self.assertIn('🏷️ R$ 30 OFF acima de R$ 169', kwargs['data']['caption'])
            self.assertIn('<b>🎟️ Opção 1</b>\n' + alert['entries'][0]['affiliate_url'],
                          kwargs['data']['caption'])
            self.assertNotIn('Resgate aqui:', kwargs['data']['caption'])
            self.assertNotIn('reply_markup', kwargs['data'])
            return response
        with patch('requests.post', side_effect=sent) as post:
            self.assertEqual(coupons.send_alert('fake', '@fake', alert, coupons.banner_path(BASE)), (42, 0))
            post.assert_called_once()
