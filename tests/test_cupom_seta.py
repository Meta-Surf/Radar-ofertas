from types import SimpleNamespace
import unittest
from ofertas_core import coupon_page_links, extract_links, safe_url, price_info
from cupons_shopee import coupon_entries

TEXT = '''Monitor Gamer Curvo AOC AGON G4 27" QUAD QHD 180Hz 0.5ms - (Shopee)

Resgate aqui o cupom de R$ 100 OFF
👉 https://s.shopee.com.br/5fnTYcOOqj

Compre aqui:
👉 https://desconto.games/fhxh34L
R$ 1.103,08 no PIX'''
CUPON = 'https://s.shopee.com.br/5fnTYcOOqj'
PRODUCT = 'https://desconto.games/fhxh34L'

class CouponArrowTests(unittest.TestCase):
    def test_actual_aoc_offer_separates_coupon_from_product(self):
        msg = SimpleNamespace(raw_text=TEXT, get_entities_text=lambda: [], reply_markup=None)
        entries = coupon_entries([msg])
        self.assertEqual(entries, [{'url': CUPON, 'conditions': 'Resgate aqui o cupom de R$ 100 OFF'}])
        excluded = coupon_page_links(TEXT) | {e['url'] for e in entries}
        self.assertEqual([u for u in extract_links(msg) if safe_url(u) and u not in excluded], [PRODUCT])
        self.assertEqual(price_info(TEXT), {'price': '1.103,08', 'price_condition': 'no PIX', 'price_from': False})

    def test_decorative_prefixes_do_not_clear_coupon_context(self):
        for prefix in ['👉 ', '➡️ ', '🔗 ', '- ', '* ', '**👉 ', '']:
            with self.subTest(prefix=prefix):
                self.assertEqual(coupon_page_links(TEXT.replace('👉 ', prefix)), {CUPON})

    def test_product_heading_still_ends_coupon_scope(self):
        self.assertEqual(coupon_page_links('Resgate aqui o cupom de R$ 100 OFF\nCompre aqui:\n👉 '+PRODUCT), set())
