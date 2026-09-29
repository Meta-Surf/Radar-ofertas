import unittest
from unittest.mock import Mock

import mercadolivre_auto as auto

SOCIAL = 'https://www.mercadolivre.com.br/social/bebidastaio'
DIRECT = 'https://www.mercadolivre.com.br/scanner/up/MLBU1458199008'
IMAGE = 'https://http2.mlstatic.com/D_Q_NP_2X_TEST-V.webp'


class SocialCompleteCardTests(unittest.TestCase):
    def test_returns_current_price_and_image_from_complete_card(self):
        page, controls, control = Mock(), Mock(), Mock()
        page.locator.return_value = controls
        controls.count.return_value = 1
        controls.nth.return_value = control
        control.inner_text.return_value = 'Ir para produto'
        control.evaluate.return_value = {
            'href': DIRECT,
            'title': 'Scanner Automotivo Bluetooth Obd2 Eml 327 Android Ios Obdii',
            'fraction': '20',
            'cents': '90',
            'imageCandidates': [IMAGE],
        }
        result = auto.browser_social_featured(page, SOCIAL)
        self.assertEqual(result['price'], '20,90')
        self.assertEqual(result['api_image'], IMAGE)
        self.assertEqual(result['resolved_url'], DIRECT)


if __name__ == '__main__':
    unittest.main()
