import unittest

from ofertas_core import product


class MercadoLivreProductIdentityTests(unittest.TestCase):
    def test_deal_filter_is_not_used_as_product_id(self):
        url = (
            'https://www.mercadolivre.com.br/creatina-growth-supplements-'
            'monohidratada-em-po-sem-sabor-x-250g/p/MLB19603205?'
            'pdp_filters=deal%3AMLB779362-1&wid=MLB5872060016'
        )
        self.assertEqual(
            product(url),
            (
                'MercadoLivre:5872060016',
                'Mercado Livre',
                'https://produto.mercadolivre.com.br/MLB-5872060016-_JM',
            ),
        )

    def test_catalog_path_wins_when_only_deal_filter_exists(self):
        url = (
            'https://www.mercadolivre.com.br/produto/p/MLB19603205?'
            'pdp_filters=deal%3AMLB779362-1'
        )
        self.assertEqual(
            product(url),
            (
                'MercadoLivre:19603205',
                'Mercado Livre',
                'https://www.mercadolivre.com.br/p/MLB19603205',
            ),
        )

    def test_item_id_filter_is_still_supported(self):
        url = (
            'https://www.mercadolivre.com.br/produto/up/MLBU3669698149?'
            'pdp_filters=item_id%3AMLB4360643061'
        )
        self.assertEqual(
            product(url),
            (
                'MercadoLivre:4360643061',
                'Mercado Livre',
                'https://produto.mercadolivre.com.br/MLB-4360643061-_JM',
            ),
        )

    def test_explicit_item_id_beats_other_query_ids(self):
        url = (
            'https://www.mercadolivre.com.br/produto/p/MLB19603205?'
            'item_id=MLB5555555555&wid=MLB5872060016&'
            'pdp_filters=deal%3AMLB779362-1'
        )
        self.assertEqual(product(url)[0], 'MercadoLivre:5555555555')


if __name__ == '__main__':
    unittest.main()
