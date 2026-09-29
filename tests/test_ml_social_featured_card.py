import os
import unittest
from unittest.mock import Mock, patch

import mercadolivre_auto as auto
from shopee_afiliados import AffiliateError

SOCIAL = 'https://www.mercadolivre.com.br/social/bebidastaio'
DIRECT = 'https://www.mercadolivre.com.br/camera/p/MLB123456'
IMAGE = 'https://http2.mlstatic.com/D_NQ_NP_TEST-O.webp'


class SocialFeaturedCardTests(unittest.TestCase):
    def setUp(self):
        p = patch.dict(os.environ, {'ML_MANUAL_CHAT': '-1003988174916'})
        p.start()
        self.addCleanup(p.stop)

    def test_reads_featured_card_without_opening_product(self):
        page, controls, control = Mock(), Mock(), Mock()
        page.locator.return_value = controls
        controls.count.return_value = 1
        controls.nth.return_value = control
        control.inner_text.return_value = 'Ir para produto'
        control.evaluate.return_value = {
            'href': DIRECT,
            'title': 'Camera IM7+ 3MP Intelbras',
            'fraction': '149',
            'cents': '99',
            'image': IMAGE,
        }
        result = auto.browser_social_featured(page, SOCIAL)
        self.assertEqual(result['price'], '149,99')
        self.assertEqual(result['resolved_url'], DIRECT)
        self.assertEqual(result['api_image'], IMAGE)
        self.assertTrue(result['social_featured'])

    def test_blocks_non_product_href(self):
        page, controls, control = Mock(), Mock(), Mock()
        page.locator.return_value = controls
        controls.count.return_value = 1
        controls.nth.return_value = control
        control.inner_text.return_value = 'Ir para produto'
        control.evaluate.return_value = {
            'href': SOCIAL,
            'title': 'Produto',
            'fraction': '149',
            'cents': '99',
            'image': IMAGE,
        }
        with self.assertRaises(AffiliateError):
            auto.browser_social_featured(page, SOCIAL)


if __name__ == '__main__':
    unittest.main()
