"""Regressões para preços escritos nas mensagens, sem estimativas."""
import unittest
from unittest.mock import patch

from ofertas_core import price, price_info, caption
from shopee_afiliados import ShopeeAffiliate


class GroupPriceTests(unittest.TestCase):
    def test_exact_user_example_through_affiliate_and_caption(self):
        text = ('🔥 Placa de Vídeo Zotac RTX 5060 Twin Edge OC, 8GB, GDDR7, 128-bit, ZT-B50600H-10M\n\n'
                '💵 R$ 2391 no app\n\n✨ Link do produto:\nhttps://s.shopee.com.br/2Vs1xQCJZx\n\n'
                'Conheça nossos grupos no WhatsApp\nwww.cacaprecodorocha.com.br')
        info = price_info(text)
        self.assertEqual(info, {'price': '2.391,00', 'price_condition': 'no app', 'price_from': False})
        offer = dict(info, store='Shopee', source='telegram', product_id='Shopee:1:2',
                     url='https://shopee.com.br/product/1/2')
        client = ShopeeAffiliate('123', 'secret')
        with patch.object(client, 'generate_link', return_value='https://s.shopee.com.br/novo'), \
             patch.object(client, 'details', return_value={'productName': 'Zotac RTX 5060'}):
            rendered = caption(client.prepare(offer))
        self.assertIn('R$ 2.391,00</b>\n\nno app', rendered)
        self.assertNotIn('WhatsApp', rendered)

    def test_common_formats(self):
        cases = [
            ('💵 R$ 2391 no app', '2.391,00', 'no app'),
            ('🔥 R$ 2.391,90 no Pix', '2.391,90', 'no Pix'),
            ('💸 **R$ 2.391,90** à vista', '2.391,90', 'à vista'),
            ('🤑 R$2391.90', '2.391,90', ''),
            ('R$ 1,234', '1.234,00', ''),
            ('Preço: R$ 2391', '2.391,00', ''),
            ('Valor final: R$ 2391 no aplicativo', '2.391,00', 'no aplicativo'),
            ('Por: R$ 2391 usando o cupom TESTE10', '2.391,00', 'usando o cupom TESTE10'),
            ('De R$ 3000 por R$ 2391 no app', '2.391,00', 'no app'),
            ('R$\u00a02.391,00 via Pix', '2.391,00', 'via Pix'),
            ('R$ 2391 com cupom de R$ 100', '2.391,00', 'com cupom de R$ 100'),
            ('~~R$ 3000~~\nR$ 2391', '2.391,00', ''),
        ]
        for text, value, condition in cases:
            with self.subTest(text=text):
                info = price_info(text)
                self.assertIsNotNone(info)
                self.assertEqual((info['price'], info['price_condition']), (value, condition))

    def test_does_not_use_shipping_discount_installments_or_minimum(self):
        for text in ['Frete R$ 10', 'Frete por R$ 10', 'Cupom de R$ 100',
                     'Cupom por R$ 20', 'R$ 10 OFF em R$ 119',
                     'Cashback de R$ 30', '10x de R$ 239,10', '10x por R$ 239,10',
                     'Frete:\nR$ 10', 'Cupom:\nR$ 20',
                     'R$ 20 por mês', 'Compras acima de R$ 100',
                     'De R$ 3000', 'R$ 1,23,4', 'R$ 0,00']:
            with self.subTest(text=text):
                self.assertIsNone(price(text))

    def test_keeps_product_price_when_other_amounts_exist(self):
        text = '💵 R$ 2391 no app\nCupom de R$ 100\nFrete R$ 20\n10x de R$ 239,10'
        self.assertEqual(price(text), '2.391,00')

    def test_ambiguous_prices_are_not_guessed(self):
        for text in ['R$ 2391 no Pix\nR$ 2500 no cartão', 'Por R$ 100\nPor R$ 200']:
            self.assertIsNone(price(text))

    def test_from_price_keeps_qualifier(self):
        info = price_info('A partir de R$ 99,90 no app')
        self.assertTrue(info['price_from'])
        self.assertIn('a partir de R$ 99,90', caption(dict(info, store='Shopee')))

    def test_price_condition_html_is_escaped(self):
        info = price_info('R$ 2391 com cupom <TESTE>')
        self.assertIn('&lt;TESTE&gt;', caption(dict(info, store='Shopee')))


if __name__ == '__main__':
    unittest.main()

