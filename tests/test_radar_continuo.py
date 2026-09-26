"""Verifica categorias e rotação do radar sem acessar APIs externas."""
import unittest

from radar_shopee_continuo import TEMAS, REGRAS_TEMA, pertence_ao_tema, select


class RadarContinuoTests(unittest.TestCase):
    def test_categories_and_accessory_exclusion(self):
        self.assertEqual(len(TEMAS), 22)
        self.assertEqual({theme for theme, _ in TEMAS}, set(REGRAS_TEMA))
        cases = [
            ('Televisores', 'Smart TV 55 polegadas', True),
            ('Televisores', 'Suporte para smart TV 55', False),
            ('Caixas de som', 'Caixa de som bluetooth 40W', True),
            ('Caixas de som', 'Capa para caixa de som bluetooth', False),
            ('Acessórios de informática', 'Hub USB 3.0 4 portas', True),
            ('Consoles', 'Controle para Playstation 5', False),
        ]
        for theme, title, expected in cases:
            with self.subTest(theme=theme, title=title):
                self.assertEqual(bool(pertence_ao_tema(theme, {'name': title})), expected)

    def test_loop_respects_theme_rotation(self):
        def offer(item, rating):
            return {'product_id': item, 'rating': rating, 'sales': 100, 'discount': 30}
        groups = [('Caixas de som', [offer('Shopee:1:1', 4.5)]),
                  ('Monitores', [offer('Shopee:2:2', 5.0)])]
        chosen = select(groups, 1, preserve_order=True)
        self.assertEqual(chosen[0][0], 'Caixas de som')


if __name__ == '__main__':
    unittest.main()
