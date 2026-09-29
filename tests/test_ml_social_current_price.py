import unittest
from unittest.mock import Mock

import mercadolivre_auto as auto
from shopee_afiliados import AffiliateError

SOCIAL = 'https://www.mercadolivre.com.br/social/bebidastaio'
DIRECT = 'https://www.mercadolivre.com.br/produto/up/MLBU1458199008'
IMAGE = 'https://http2.mlstatic.com/D_Q_NP_2X_TEST-V.webp'


class SocialCurrentPriceTests(unittest.TestCase):
    def _run(self, current_text, card_text):
        page, controls, control = Mock(), Mock(), Mock()
        page.locator.return_value = controls
        controls.count.return_value = 1
        controls.nth.return_value = control
        control.inner_text.return_value = 'Ir para produto'

        def evaluate(script):
            self.assertIn('.poly-price__current', script)
            self.assertIn('currentPriceText', script)
            self.assertIn('cardText', script)
            return {
                'href': DIRECT,
                'title': 'Produto Teste',
                'currentPriceText': current_text,
                'cardText': card_text,
                'imageCandidates': [IMAGE],
            }

        control.evaluate.side_effect = evaluate
        return auto.browser_social_featured(page, SOCIAL)

    def test_uses_product_current_price_not_coupon_value(self):
        result = self._run(
            'R$ 135,99 32% OFF',
            'R$ 199,90 R$ 135,99 32% OFF 10% OFF com Saldo no Mercado Pago',
        )
        self.assertEqual(result['price'], '135,99')

    def test_uses_product_current_price_not_installment_value(self):
        result = self._run(
            '10x R$ 489,00 sem juros',
            'R$ 4.399 R$ 3.912 11% OFF no Pix ou R$ 4.890 em 10x R$ 489,00 sem juros',
        )
        self.assertEqual(result['price'], '3.912,00')

    def test_exact_s25_case_uses_4958_not_55090_installment(self):
        result = self._run(
            '10x R$ 550,90 sem juros',
            'R$ 10.499 R$ 4.958 52% OFF no Pix ou R$ 5.509 em 10x '
            'R$ 550,90 sem juros 10% OFF com Saldo no Mercado Pago',
        )
        self.assertEqual(result['price'], '4.958,00')

    def test_total_price_without_discount_beats_installment(self):
        self.assertEqual(
            auto.social_featured_price(
                'R$ 5.509 em 10x R$ 550,90 sem juros',
                'R$ 5.509 em 10x R$ 550,90 sem juros',
            ),
            '5.509,00',
        )

    def test_only_installment_is_blocked(self):
        with self.assertRaises(AffiliateError):
            auto.social_featured_price(
                '10x R$ 550,90 sem juros',
                '10x R$ 550,90 sem juros',
            )


if __name__ == '__main__':
    unittest.main()
