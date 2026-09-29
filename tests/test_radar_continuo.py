"""Verifica categorias, filtros e rotação do radar sem acessar APIs externas."""
import unittest

from radar_shopee_continuo import (
    REGRAS_TEMA,
    load_catalog,
    pertence_ao_tema,
    select,
    specs_for_round,
)


class RadarContinuoTests(unittest.TestCase):
    def test_categories_and_accessory_exclusion(self):
        groups, themes = load_catalog()
        self.assertGreaterEqual(len(themes), 42)
        self.assertEqual({spec['theme'] for spec in themes}, set(REGRAS_TEMA))
        self.assertEqual(groups, ['Tecnologia', 'Games', 'Eletrodomesticos', 'Casa', 'Ferramentas', 'Automotivo'])
        cases = [
            ('Televisores', 'Smart TV 55 polegadas', True),
            ('Televisores', 'Suporte para smart TV 55', False),
            ('Caixas de som', 'Caixa de som bluetooth 40W', True),
            ('Caixas de som', 'Capa para caixa de som bluetooth', False),
            ('Acessórios de informática', 'Hub USB 3.0 4 portas', True),
            ('Consoles', 'Controle para Playstation 5', False),
            ('Consoles', 'Console PlayStation 5 Slim 1TB', True),
            ('Controles de videogame', 'Controle Sony DualSense PS5', True),
            ('Controles de videogame', 'Capa para controle DualSense', False),
        ]
        for theme, title, expected in cases:
            with self.subTest(theme=theme, title=title):
                self.assertEqual(bool(pertence_ao_tema(theme, {'name': title})), expected)

    def test_group_rotation(self):
        groups, themes = load_catalog()
        first, specs0 = specs_for_round(groups, themes, round_index=0, loop=True)
        second, specs1 = specs_for_round(groups, themes, round_index=1, loop=True)
        self.assertEqual(first, 'Tecnologia')
        self.assertEqual(second, 'Games')
        self.assertTrue(specs0 and all(spec['group'] == 'Tecnologia' for spec in specs0))
        self.assertTrue(specs1 and all(spec['group'] == 'Games' for spec in specs1))

    def test_select_can_preserve_order(self):
        def offer(item, rating):
            return {'product_id': item, 'rating': rating, 'sales': 100, 'discount': 30}
        groups = [('Caixas de som', [offer('Shopee:1:1', 4.5)]),
                  ('Monitores', [offer('Shopee:2:2', 5.0)])]
        chosen = select(groups, 1, preserve_order=True)
        self.assertEqual(chosen[0][0], 'Caixas de som')


if __name__ == '__main__':
    unittest.main()
