import unittest
from unittest.mock import Mock

import mercadolivre_auto as auto

SOCIAL = 'https://www.mercadolivre.com.br/social/bebidastaio'
DIRECT = 'https://www.mercadolivre.com.br/produto/up/MLBU1458199008'
IMAGE = 'https://http2.mlstatic.com/D_Q_NP_2X_TEST-V.webp'


class SocialCurrentPriceTests(unittest.TestCase):
    def _run(self, fraction, cents):
        page, controls, control = Mock(), Mock(), Mock()
        page.locator.return_value = controls
        controls.count.return_value = 1
        controls.nth.return_value = control
        control.inner_text.return_value = 'Ir para produto'

        def evaluate(script):
            self.assertIn('.poly-price__current', script)
            self.assertIn('.andes-money-amount:not(.andes-money-amount--previous)', script)
            return {
                'href': DIRECT,
                'title': 'Produto Teste',
                'fraction': fraction,
                'cents': cents,
                'imageCandidates': [IMAGE],
            }

        control.evaluate.side_effect = evaluate
        return auto.browser_social_featured(page, SOCIAL)

    def test_uses_product_current_price_not_coupon_value(self):
        result = self._run('135', '99')
        self.assertEqual(result['price'], '135,99')

    def test_uses_product_current_price_not_installment_value(self):
        result = self._run('489', '')
        self.assertEqual(result['price'], '489,00')


if __name__ == '__main__':
    unittest.main()
