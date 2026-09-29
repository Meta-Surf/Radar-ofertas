import unittest
from unittest.mock import Mock

import mercadolivre_auto as auto
from shopee_afiliados import AffiliateError

SOCIAL = 'https://www.mercadolivre.com.br/social/bebidastaio'
DIRECT = 'https://www.mercadolivre.com.br/camera/p/MLB123456'
IMAGE = 'https://http2.mlstatic.com/D_NQ_NP_TEST-O.webp'


class SocialImageTests(unittest.TestCase):
    def test_social_image_accepts_srcset_and_skips_non_ml(self):
        values = [
            'data:image/gif;base64,AAAA',
            'https://evil.test/a.webp 1x, ' + IMAGE + ' 2x',
        ]
        self.assertEqual(auto.social_image_url(values), IMAGE)

    def test_social_image_rejects_missing_ml_image(self):
        with self.assertRaises(AffiliateError):
            auto.social_image_url(['data:image/gif;base64,AAAA', 'https://evil.test/a.webp 2x'])

    def test_featured_card_uses_image_candidates(self):
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
            'imageCandidates': ['data:image/gif;base64,AAAA', IMAGE + ' 1x'],
        }
        result = auto.browser_social_featured(page, SOCIAL)
        self.assertEqual(result['price'], '149,99')
        self.assertEqual(result['api_image'], IMAGE)
        self.assertEqual(result['resolved_url'], DIRECT)


if __name__ == '__main__':
    unittest.main()
