"""Testes locais da expansão do radar, sem chamadas à API ou Telegram."""
import time
import unittest

from radar_shopee import candidate
from radar_shopee_continuo import (
    load_catalog,
    pertence_ao_tema,
    premium_match,
    specs_for_round,
)


class RadarExpansionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.groups, cls.themes = load_catalog()
        cls.by_name = {spec['theme']: spec for spec in cls.themes}

    def test_catalog_has_expected_scope(self):
        self.assertGreaterEqual(len(self.themes), 42)
        self.assertEqual(
            self.groups,
            ['Tecnologia', 'Games', 'Eletrodomesticos', 'Casa', 'Ferramentas', 'Automotivo'])
        self.assertEqual(len(self.by_name), len(self.themes))

    def test_console_queries_are_specific(self):
        queries = self.by_name['Consoles']['queries']
        self.assertIn('PS5 Pro', queries)
        self.assertIn('Xbox Series X', queries)
        self.assertIn('Nintendo Switch 2', queries)
        self.assertIn('Steam Deck', queries)
        self.assertIn('ROG Ally', queries)
        self.assertNotIn('console videogame', [q.lower() for q in queries])

    def test_console_accessory_is_rejected(self):
        self.assertTrue(pertence_ao_tema('Consoles', {'name': 'Console PlayStation 5 Slim PS5 1TB'}))
        self.assertFalse(pertence_ao_tema('Consoles', {'name': 'Capa protetora para PS5 Slim'}))
        self.assertFalse(pertence_ao_tema('Consoles', {'name': 'Controle DualSense para PlayStation 5'}))

    def test_original_controller_filter(self):
        self.assertTrue(pertence_ao_tema(
            'Controles de videogame', {'name': 'Controle Sony DualSense PS5 Branco'}))
        self.assertFalse(pertence_ao_tema(
            'Controles de videogame', {'name': 'Controle compatível com PS5 estilo DualSense'}))

    def test_minimum_price_is_part_of_api_candidate(self):
        now = int(time.time())
        node = {
            'shopId': '1', 'itemId': '2', 'productName': 'Smart TV Samsung 55',
            'imageUrl': 'https://cf.shopee.com.br/file/example',
            'priceMin': '499.99', 'priceMax': '499.99',
            'priceDiscountRate': '30', 'ratingStar': '4.8', 'sales': '500',
            'periodStartTime': now - 60, 'periodEndTime': now + 3600,
        }
        self.assertIsNone(candidate(
            node, minimum_discount=15, minimum_rating=4.5,
            minimum_sales=50, minimum_price=500, now=now))
        node['priceMin'] = node['priceMax'] = '500.00'
        self.assertIsNotNone(candidate(
            node, minimum_discount=15, minimum_rating=4.5,
            minimum_sales=50, minimum_price=500, now=now))

    def test_group_rotation(self):
        name0, specs0 = specs_for_round(self.groups, self.themes, 0, loop=True)
        name1, specs1 = specs_for_round(self.groups, self.themes, 1, loop=True)
        self.assertEqual(name0, 'Tecnologia')
        self.assertEqual(name1, 'Games')
        self.assertTrue(specs0 and all(s['group'] == 'Tecnologia' for s in specs0))
        self.assertTrue(specs1 and all(s['group'] == 'Games' for s in specs1))

    def test_premium_is_product_specific(self):
        spec = self.by_name['Televisores']
        self.assertTrue(premium_match(spec, {'name': 'Smart TV LG OLED 55 polegadas'}))
        self.assertFalse(premium_match(spec, {'name': 'Smart TV LED 32 polegadas'}))


if __name__ == '__main__':
    unittest.main()
