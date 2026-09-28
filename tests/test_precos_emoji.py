import unittest
from ofertas_core import price_info, caption

class EmojiPriceTests(unittest.TestCase):
    def test_real_message(self):
        text='🆘Placa de Video GeForce NVIDIA PALIT RTX5060TI 8GB WHITE OC GDDR7\n\n💵2,943\n\nLink\nhttps://s.shopee.com.br/50ZOjewACZ\nhttps://s.shopee.com.br/50ZOjewACZ'
        self.assertEqual(price_info(text), {'price':'2.943,00','price_condition':'','price_from':False})

    def test_explicit_formats(self):
        for text, expected in [('💵2,943','2.943,00'),('💸 2,943','2.943,00'),('💰 2943','2.943,00'),('💵 R$ 2.943,00','2.943,00'),('R$ 2,943.90','2.943,90'),('💵 29,90','29,90'),('💵 1,234,567','1.234.567,00')]:
            with self.subTest(text=text): self.assertEqual(price_info(text)['price'],expected)

    def test_no_accidental_product_numbers_or_discounts(self):
        for text in ['RTX5060TI 8GB GDDR7','2,943','Cupom 💵2,943','Frete 💵20','10x 💵29,90','💵 20 OFF','Desconto:\n💵20','💵2,94,3','💵2,943\n💵2,800']:
            with self.subTest(text=text): self.assertIsNone(price_info(text))

    def test_condition_and_existing_price(self):
        self.assertEqual(price_info('💵2,943 no pix')['price_condition'],'no pix')
        self.assertEqual(price_info('🔥 Por: **R$ 2.713,08** no pix')['price'],'2.713,08')
        text=caption(dict(store='Shopee',**price_info('💵2,943 no pix')))
        self.assertIn('💰 <b>R$ 2.943,00</b>\n\nno pix',text)
