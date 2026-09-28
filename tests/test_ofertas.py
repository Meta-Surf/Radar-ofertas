import unittest
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace
from ofertas_core import product, safe_url, extract_links, price, coupon, caption, Ledger, coupon_page_links, embedded_product, page_product, resolve

class Tests(unittest.TestCase):
    def test_confirmed_short_link_without_network(self):
        from unittest.mock import Mock, patch
        network = Mock()
        with patch.dict('sys.modules', {'requests':network}):
            self.assertEqual(resolve('https://meli.la/2PnnX9t')[0], 'MercadoLivre:4360643061')
            self.assertEqual(resolve('https://meli.la/2PnnX9t/#fragment')[0], 'MercadoLivre:4360643061')
            network.get.assert_not_called()
    def test_unknown_short_link_does_not_reuse_product(self):
        from unittest.mock import Mock, MagicMock, patch
        response = MagicMock(status_code=403)
        response.__enter__.return_value = response
        network = Mock()
        network.get.return_value = response
        with patch.dict('sys.modules', {'requests':network}):
            self.assertIsNone(resolve('https://meli.la/outro'))
            self.assertEqual(network.get.call_count, 1)

    def test_opaanlp_same_product(self):
        self.assertEqual(product('https://shopee.com.br/opaanlp/627750190/22899341907?utm_source=test'),
                         product('https://shopee.com.br/product/627750190/22899341907'))
        self.assertIsNone(product('https://shopee.com.br/opaanlp/627750190/22899341907/extra'))
    def test_redirect_to_opaanlp(self):
        from unittest.mock import Mock, MagicMock, patch
        response = MagicMock(status_code=301, headers={'Location':'https://shopee.com.br/opaanlp/627750190/22899341907?utm_source=test'})
        response.__enter__.return_value = response
        requests_mock = Mock()
        requests_mock.get.return_value = response
        with patch.dict('sys.modules', {'requests':requests_mock}):
            self.assertEqual(resolve('https://s.shopee.com.br/2qUoBe1V4I?lp=aff')[0], 'Shopee:627750190:22899341907')
            self.assertEqual(requests_mock.get.call_count, 1)
    def test_query_product_ids(self):
        self.assertEqual(embedded_product('https://shopee.com.br/universal-link?shopid=123&itemid=456')[0], 'Shopee:123:456')
    def test_wrapped_product(self):
        self.assertEqual(embedded_product('https://shopee.com.br/universal-link?url=https%3A%2F%2Fshopee.com.br%2Fproduct%2F123%2F456')[0], 'Shopee:123:456')
    def test_canonical_and_recommendation(self):
        page = '<link rel="canonical" href="https://shopee.com.br/product/123/456"><a href="https://shopee.com.br/product/999/888">Recommended</a>'
        self.assertEqual(page_product(page, 'https://shopee.com.br/')[0], 'Shopee:123:456')
    def test_conflicting_metadata(self):
        page = '<link rel="canonical" href="https://shopee.com.br/product/123/456"><meta property="og:url" content="https://shopee.com.br/product/999/888">'
        self.assertIsNone(page_product(page, 'https://shopee.com.br/'))
    def test_no_product_metadata(self):
        self.assertIsNone(page_product('<title>Verification</title>', 'https://shopee.com.br/'))
    def test_redirect_then_html(self):
        from unittest.mock import Mock, MagicMock, patch
        first = MagicMock(status_code=301, headers={'Location':'https://shopee.com.br/landing'})
        first.__enter__.return_value = first
        second = MagicMock(status_code=200)
        second.__enter__.return_value = second
        second.iter_content.return_value = [b'<meta property="og:url" content="https://shopee.com.br/product/123/456">']
        requests_mock = Mock()
        requests_mock.get.side_effect = [first, second]
        with patch.dict('sys.modules', {'requests':requests_mock}):
            self.assertEqual(resolve('https://s.shopee.com.br/test')[0], 'Shopee:123:456')
    def test_integer_price(self):
        self.assertEqual(price('✅ R$ 3368'), '3.368,00')
        self.assertEqual(price('✅ R$ 3.368,90'), '3.368,90')
    def test_coupon_page_separate_from_product(self):
        text = '🎟️ Resgate todos os cupons desta página:\nhttps://s.shopee.com.br/1VzQbBxdgM\n\nLINK LINK: https://s.shopee.com.br/2qUoBe1V4I?lp=aff'
        self.assertEqual(coupon_page_links(text), {'https://s.shopee.com.br/1VzQbBxdgM'})
        self.assertIsNone(coupon(text))
    def test_shopee(self):
        self.assertEqual(product('https://shopee.com.br/nome-i.123.456?utm_source=x'), product('https://shopee.com.br/product/123/456'))
    def test_amazon(self):
        self.assertEqual(product('https://www.amazon.com.br/gp/product/B012345678?tag=outro-20')[2], 'https://www.amazon.com.br/dp/B012345678')
    def test_ml(self):
        self.assertEqual(product('https://www.mercadolivre.com.br/p/MLB999?pdp_filters=item_id:MLB123')[0], 'MercadoLivre:123')
    def test_host(self):
        for u in ['https://shopee.com.br.evil.test/product/1/2','http://shopee.com.br/product/1/2','https://127.0.0.1/','https://shopee.com.br@evil.test/']:
            self.assertFalse(safe_url(u))
    def test_links(self):
        a,b='https://shopee.com.br/product/1/2','https://www.amazon.com.br/dp/B012345678'
        msg=SimpleNamespace(raw_text='', get_entities_text=lambda:[(SimpleNamespace(url=a),'compre')], reply_markup=SimpleNamespace(rows=[SimpleNamespace(buttons=[SimpleNamespace(url=b)])]))
        self.assertEqual(extract_links(msg),[a,b])
    def test_coupon(self):
        self.assertEqual(coupon('Use o cupom: CASA10'),'CASA10')
        self.assertIsNone(coupon('Cupom disponível no aplicativo'))
        self.assertIsNone(coupon('Cupons na loja'))
    def test_price(self):
        self.assertEqual(price('De R$ 199,90 por R$ 99,90'),'99,90')
        self.assertIsNone(price('Frete R$ 10,00'))
        self.assertIsNone(price('Cupom de R$ 20,00'))
    def test_escape(self):
        self.assertIn('&lt;b&gt;',caption({'store':'Shopee','coupon':'<b>10</b>'}))
    def test_restart_and_two_connections(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'ledger.db'
            a,b=Ledger(p),Ledger(p)
            moment=datetime(2026,9,24,12,tzinfo=timezone.utc)
            self.assertIsNotNone(a.reserve('x',moment))
            self.assertIsNone(b.reserve('x',moment))
            a.db.close()
            c=Ledger(p)
            self.assertIsNone(c.reserve('x',moment))
            b.db.close(); c.db.close()
    def test_local_day(self):
        with tempfile.TemporaryDirectory() as d:
            l=Ledger(Path(d)/'ledger.db')
            self.assertEqual(l.reserve('x',datetime(2026,9,24,2,59,tzinfo=timezone.utc)),'2026-09-23')
            self.assertIsNone(l.reserve('x',datetime(2026,9,24,3,1,tzinfo=timezone.utc)))
            l.db.close()
    def test_shopee_price_explains_checkout_discounts(self):
        text = caption({'store':'Shopee','source':'shopee_api','price':'879,99'})
        self.assertIn('Antes de cupons e descontos de pagamento.', text)
        self.assertIn('possíveis descontos no Pix', text)
        self.assertNotIn('713,00', text)
    def test_rejection_and_success(self):
        with tempfile.TemporaryDirectory() as d:
            l=Ledger(Path(d)/'ledger.db')
            day=l.reserve('x'); l.release('x',day)
            self.assertIsNotNone(l.reserve('x'))
            l.finish('x',day,123); l.release('x',day)
            self.assertIsNone(l.reserve('x'))
            l.db.close()

if __name__=='__main__':
    unittest.main()

