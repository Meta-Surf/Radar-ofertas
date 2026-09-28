from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from ofertas_core import safe_url, product, resolve, price_info, extract_links, caption, coupon
from cupons_shopee import coupon_entries
from shopee_afiliados import ShopeeAffiliate

EXAMPLE = '''Monitor LG UltraWide 34" 34U620B-B WQHD 2K, Curvatura 1800R 144Hz HDR 10 ( Shopee)

- ative o cupom de 5% OFF no carrinho

Compre aqui:
👉 https://desconto.games/5MqFmad
R$ 1.661,39 no PIX

🕹 Grupos de Promoções da DG
👉 https://godg.me/WkQUSso'''


def response(code, location=None):
    r = MagicMock(status_code=code, headers={'Location': location} if location else {})
    r.__enter__.return_value = r
    return r


class ProductRedirectTests(unittest.TestCase):
    def test_source_selects_product_link_without_promotional_group(self):
        msg = SimpleNamespace(raw_text=EXAMPLE, get_entities_text=lambda: [], reply_markup=None)
        self.assertEqual([u for u in extract_links(msg) if safe_url(u)], ['https://desconto.games/5MqFmad'])
        self.assertEqual(coupon_entries([msg]), [])
        self.assertIsNone(product('https://desconto.games/5MqFmad'))
        self.assertIsNone(coupon(EXAMPLE))

    def test_price_keeps_pix_and_required_coupon_without_calculation(self):
        info = price_info(EXAMPLE)
        self.assertEqual(info['price'], '1.661,39')
        self.assertEqual(info['price_condition'], 'no PIX; ative o cupom de 5% OFF no carrinho')
        text = caption(dict(info, store='Shopee', source='telegram'))
        self.assertIn('R$ 1.661,39</b>', text)
        self.assertIn('5% OFF no carrinho', text)
        self.assertNotIn('godg.me', text)

    def test_redirect_chain_to_canonical_then_own_affiliate_link(self):
        with patch('requests.get', side_effect=[
            response(302, 'https://s.shopee.com.br/exemplo'),
            response(301, 'https://shopee.com.br/product/123/456?utm_source=third_party')]) as get:
            found = resolve('https://desconto.games/5MqFmad')
            self.assertEqual(found, ('Shopee:123:456', 'Shopee', 'https://shopee.com.br/product/123/456'))
            self.assertEqual(get.call_count, 2)
            self.assertTrue(all(c.kwargs['allow_redirects'] is False for c in get.call_args_list))
        client = ShopeeAffiliate('123', 'fake')
        with patch.object(client, 'generate_link', return_value='https://s.shopee.com.br/meu-link') as link, \
             patch.object(client, 'details', return_value={'productName': 'Monitor LG'}):
            offer = dict(price_info(EXAMPLE), product_id=found[0], store=found[1], url=found[2], source='telegram')
            ready = client.prepare(offer)
            self.assertTrue(ready['affiliate_generated'])
            self.assertEqual(ready['affiliate_url'], 'https://s.shopee.com.br/meu-link')
            link.assert_called_once_with(found[2])

    def test_relative_redirect(self):
        with patch('requests.get', side_effect=[response(302, '/destino'), response(302, 'https://shopee.com.br/product/123/456')]) as get:
            self.assertEqual(resolve('https://desconto.games/5MqFmad')[0], 'Shopee:123:456')
            self.assertEqual(get.call_args_list[1].args[0], 'https://desconto.games/destino')

    def test_403_keeps_diagnostic_and_does_not_guess_product(self):
        trace = []
        with patch('requests.get', return_value=response(403)) as get:
            self.assertIsNone(resolve('https://desconto.games/5MqFmad', trace.append))
            get.assert_called_once()
            self.assertTrue(any('HTTP 403' in line for line in trace))

    def test_unknown_destination_is_not_requested(self):
        for target in ['https://godg.me/WkQUSso', 'https://evil.test/path', 'http://shopee.com.br/product/1/2', 'https://127.0.0.1/']:
            with self.subTest(target=target), patch('requests.get', return_value=response(302, target)) as get:
                self.assertIsNone(resolve('https://desconto.games/5MqFmad'))
                self.assertEqual(get.call_count, 1)

    def test_loop_and_missing_location_stop_early(self):
        for reply in [response(302, 'https://desconto.games/5MqFmad'), response(302)]:
            with patch('requests.get', return_value=reply) as get:
                self.assertIsNone(resolve('https://desconto.games/5MqFmad'))
                self.assertEqual(get.call_count, 1)

    def test_untrusted_variants_and_malformed_ports_remain_rejected(self):
        for url in ['https://desconto.games.evil.test/a', 'https://desconto.games@evil.test/a',
                    'https://evil.test@desconto.games/a', 'https://desconto.games:9999/a',
                    'https://desconto.games:bad/a', 'http://desconto.games/a', 'https://godg.me/WkQUSso']:
            with self.subTest(url=url): self.assertFalse(safe_url(url))

class ConverterTests(unittest.TestCase):
    def test_registered_destination_is_used_only_for_exact_source(self):
        import tempfile
        from pathlib import Path
        from converter_link import register_destination
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'destinos_confirmados.json'
            source = 'https://desconto.games/5MqFmad'
            register_destination(source, 'https://shopee.com.br/product/123/456?utm_source=other', path)
            with patch('ofertas_core.__file__', str(Path(d) / 'ofertas_core.py')), patch('requests.get') as get:
                self.assertEqual(resolve(source)[2], 'https://shopee.com.br/product/123/456')
                get.assert_not_called()
            with patch('ofertas_core.__file__', str(Path(d) / 'ofertas_core.py')), patch('requests.get', return_value=response(403)) as get:
                self.assertIsNone(resolve('https://desconto.games/outro'))
                get.assert_called_once()

    def test_non_shopee_destination_is_rejected_without_writing(self):
        import tempfile
        from pathlib import Path
        from converter_link import register_destination
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'destinos_confirmados.json'
            for destination in ['https://evil.test/product/1/2', 'https://shopee.com.br/m/cupons', 'https://godg.me/WkQUSso']:
                with self.assertRaises(ValueError):
                    register_destination('https://desconto.games/5MqFmad', destination, path)
                self.assertFalse(path.exists())

    def test_converter_generates_own_link_and_no_publication(self):
        from converter_link import main
        found = ('Shopee:123:456', 'Shopee', 'https://shopee.com.br/product/123/456')
        with patch('converter_link.resolve', return_value=found), patch('converter_link.ShopeeAffiliate.from_env') as factory, patch('builtins.print') as output:
            factory.return_value.generate_link.return_value = 'https://s.shopee.com.br/meu-link'
            self.assertEqual(main(['https://desconto.games/5MqFmad']), 0)
            factory.return_value.generate_link.assert_called_once_with(found[2])
            self.assertIn(('Seu link de afiliado:', 'https://s.shopee.com.br/meu-link'), [call.args for call in output.call_args_list])

    def test_failed_resolution_does_not_call_affiliate_api(self):
        from converter_link import main
        with patch('converter_link.resolve', return_value=None), patch('converter_link.ShopeeAffiliate.from_env') as api, patch('builtins.print'):
            self.assertEqual(main(['https://desconto.games/5MqFmad']), 2)
            api.assert_not_called()
