import io
import json
import os
import tempfile
import unittest
from concurrent.futures import Future
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import mercadolivre_auto as auto
import mercadolivre_manual as manual
import bot_ofertas_revisao as publisher
from ofertas_core import Ledger
from shopee_afiliados import AffiliateError

URL = 'https://meli.la/2wpg8CQ'
DIRECT = 'https://www.mercadolivre.com.br/camera/p/MLB123456'
IMAGE = 'https://http2.mlstatic.com/D_NQ_NP_TEST-O.webp'
# Fixture sintética: o teste não afirma que estes são o destino/preço reais do link.
DATA = {'@context': 'https://schema.org', '@type': 'Product', 'name': 'Câmera IM7+ 3MP Intelbras',
        'url': DIRECT, 'image': [IMAGE],
        'offers': {'@type': 'Offer', 'price': '199.90', 'priceCurrency': 'BRL',
                   'availability': 'https://schema.org/InStock'}}


def document(data=None):
    return '<script type="application/ld+json">' + json.dumps(DATA if data is None else data) + '</script>'


def row(text=URL):
    message = SimpleNamespace(raw_text=text, id=7, date=datetime.now(timezone.utc),
                              get_entities_text=lambda: [], reply_markup=None)
    return manual.build_offer([message], -1003988174916)


def response(status=200, text='', headers=None):
    r = Mock(status_code=status, headers=headers or {})
    r.iter_content.return_value = [text.encode()]
    r.__enter__ = Mock(return_value=r); r.__exit__ = Mock(return_value=False)
    return r


class AutoMLTests(unittest.TestCase):
    def setUp(self):
        p = patch.dict(os.environ, {'ML_MANUAL_CHAT': '-1003988174916'})
        p.start(); self.addCleanup(p.stop)

    def test_link_only_is_queued_without_inventing_name_or_price(self):
        entry = row()
        self.assertFalse(entry['name'])
        self.assertIsNone(entry['price'])
        self.assertEqual(entry['url'], URL)

    def test_structured_product_price_image_and_graph(self):
        for data in [DATA, {'@graph': [DATA]}, [DATA]]:
            result = auto.extract_product(document(data), DIRECT)
            self.assertEqual(result['price'], '199,90')
            self.assertEqual(result['api_image'], IMAGE)
            self.assertEqual(result['name'], DATA['name'])

    def test_does_not_take_price_from_installments_strike_or_recommendations(self):
        text = '<del>999,90</del><div>12x R$ 9,99</div>' + document(DATA)
        result = auto.extract_product(text, DIRECT)
        self.assertEqual(result['price'], '199,90')
        only_list = {'@type': 'ItemList', 'itemListElement': [DATA]}
        with self.assertRaises(AffiliateError):
            auto.extract_product(document(only_list), DIRECT)

    def test_ambiguous_products_and_prices_are_blocked(self):
        other = dict(DATA, name='Outra câmera')
        for data in [[DATA, other], dict(DATA, offers=[DATA['offers'], DATA['offers']]),
                     dict(DATA, offers={'@type': 'AggregateOffer', 'lowPrice': '10', 'highPrice': '100'})]:
            with self.subTest(data=data), self.assertRaises(AffiliateError):
                auto.extract_product(document(data), DIRECT)

    def test_availability_currency_and_exact_positive_price(self):
        for changes in [{'availability': 'https://schema.org/OutOfStock'}, {'availability': None},
                        {'priceCurrency': 'USD'}, {'price': 'NaN'}, {'price': '-1'}, {'price': '0'},
                        {'price': '1.999,90'}, {'price': True}, {'price': '12.9999'}]:
            with self.subTest(changes=changes), self.assertRaises(AffiliateError):
                auto.extract_product(document(dict(DATA, offers=dict(DATA['offers'], **changes))), DIRECT)

    def test_wrong_product_and_external_image_rejected(self):
        for changes in [{'url': DIRECT.replace('123456', '999999')}, {'image': 'https://evil.test/image.jpg'}]:
            with self.subTest(changes=changes), self.assertRaises(AffiliateError):
                auto.extract_product(document(dict(DATA, **changes)), DIRECT)
        with self.assertRaises(AffiliateError):
            auto.extract_product(document(), 'https://www.mercadolivre.com.br/social/vitrine')

    def test_http_resolves_short_link_and_does_not_follow_external_redirect(self):
        transport = Mock()
        transport.get.side_effect = [response(302, headers={'Location': DIRECT}), response(text=document())]
        self.assertEqual(auto.fetch_http(URL, transport)['price'], '199,90')
        self.assertEqual(transport.get.call_count, 2)
        self.assertFalse(transport.get.call_args.kwargs['allow_redirects'])
        transport.reset_mock(); transport.get.side_effect = [response(302, headers={'Location': 'https://evil.test/x'})]
        with self.assertRaises(AffiliateError):
            auto.fetch_http(URL, transport)
        self.assertEqual(transport.get.call_count, 1)

    def test_official_ml_subdomain_redirect_is_allowed_and_uses_local_session(self):
        redirected = 'https://click.mercadolivre.com.br/produto/p/MLB123456'
        self.assertTrue(manual.allowed_link(redirected))
        self.assertFalse(manual.allowed_link('https://mercadolivre.com.br.evil.test/p/MLB123456'))
        transport = Mock()
        transport.get.side_effect = [response(302, headers={'Location': redirected}),
                                     response(text=document())]
        with patch.dict(os.environ, {'ML_READER_COOKIE': 'foo=bar; session=abc123'}, clear=False):
            self.assertEqual(auto.fetch_http(URL, transport)['price'], '199,90')
        self.assertIn('Cookie', transport.get.call_args.kwargs['headers'])

    def test_nested_official_target_is_resolved_but_external_is_ignored(self):
        wrapped = ('https://www.mercadolivre.com.br/redirect?target='
                   'https%3A%2F%2Fwww.mercadolivre.com.br%2Fcamera%2Fp%2FMLB123456')
        self.assertEqual(auto.nested_official_urls(wrapped), [DIRECT])
        evil = ('https://www.mercadolivre.com.br/redirect?target='
                'https%3A%2F%2Fevil.test%2Fp%2FMLB123456')
        self.assertEqual(auto.nested_official_urls(evil), [])
        self.assertEqual(auto.next_destination('', wrapped), DIRECT)

    def test_http_transport_preloads_cookie_into_persistent_session(self):
        session = Mock()
        session.cookies = Mock()
        with patch.object(auto.requests, 'Session', return_value=session), \
             patch.dict(os.environ, {'ML_READER_COOKIE': 'foo=bar; session=abc123'}, clear=False):
            result = auto.http_transport()
        self.assertIs(result, session)
        session.cookies.set.assert_any_call('foo', 'bar')
        session.cookies.set.assert_any_call('session', 'abc123')

    def test_reader_never_reuses_affiliate_cookie(self):
        with patch.dict(
            os.environ,
            {
                'ML_AFFILIATE_COOKIE': 'sensitive=affiliate-secret',
                'ML_READER_COOKIE': '',
            },
            clear=False,
        ):
            headers = auto.request_headers()
            self.assertNotIn('Cookie', headers)

            session = Mock()
            session.cookies = Mock()
            with patch.object(auto.requests, 'Session', return_value=session):
                auto.http_transport()
            session.cookies.set.assert_not_called()

    def test_social_profile_uses_only_unique_featured_product_cta(self):
        social = 'https://www.mercadolivre.com.br/social/bebidastaio'
        featured = DIRECT
        grid = DIRECT.replace('123456', '999999')
        text = (
            '<section><a href="' + featured + '"><span>Ir para produto</span></a></section>'
            '<div><a href="' + grid + '">Outro produto da grade</a></div>'
        )
        self.assertTrue(auto.social_page(social))
        self.assertEqual(auto.featured_social_destination(text, social), featured)
        self.assertEqual(auto.next_destination(text, social), featured)

    def test_social_profile_blocks_missing_or_ambiguous_featured_cta(self):
        social = 'https://www.mercadolivre.com.br/social/bebidastaio'
        other = DIRECT.replace('123456', '999999')
        no_cta = '<a href="' + DIRECT + '">Produto da grade</a>'
        ambiguous = (
            '<a href="' + DIRECT + '">Ir para produto</a>'
            '<a href="' + other + '"><span>Ir para o produto</span></a>'
        )
        self.assertIsNone(auto.featured_social_destination(no_cta, social))
        self.assertIsNone(auto.next_destination(no_cta, social))
        self.assertIsNone(auto.featured_social_destination(ambiguous, social))
        self.assertIsNone(auto.next_destination(ambiguous, social))

    def test_http_short_link_social_featured_then_product(self):
        social = 'https://www.mercadolivre.com.br/social/bebidastaio?ref=x'
        social_html = (
            '<a href="' + DIRECT + '"><strong>Ir para produto</strong></a>'
            '<a href="' + DIRECT.replace('123456', '999999') + '">Produto da grade</a>'
        )
        transport = Mock()
        transport.get.side_effect = [
            response(301, headers={'Location': social}),
            response(text=social_html),
            response(text=document()),
        ]
        result = auto.fetch_http(URL, transport)
        self.assertEqual(result['name'], DATA['name'])
        self.assertEqual(result['price'], '199,90')
        self.assertEqual(result['resolved_url'], DIRECT)
        self.assertEqual(transport.get.call_count, 3)

    def test_wrapper_only_follows_one_product_never_recommendation(self):
        wrapper = '<a href="' + DIRECT + '">Câmera</a>'
        self.assertEqual(auto.next_destination(wrapper, URL), DIRECT)
        self.assertIsNone(auto.next_destination(wrapper, DIRECT))
        wrapper += '<a href="' + DIRECT.replace('123456', '999999') + '">Outra</a>'
        self.assertIsNone(auto.next_destination(wrapper, URL))

    def test_browser_social_destination_uses_unique_rendered_cta_href(self):
        social = 'https://www.mercadolivre.com.br/social/bebidastaio'
        page, context, locator, control = Mock(), Mock(), Mock(), Mock()
        page.locator.return_value = locator
        locator.filter.return_value = locator
        locator.count.return_value = 1
        locator.first = control
        control.get_attribute.return_value = DIRECT
        self.assertEqual(auto.browser_social_destination(page, context, social), DIRECT)
        control.click.assert_not_called()

    def test_browser_social_destination_blocks_ambiguous_cta(self):
        social = 'https://www.mercadolivre.com.br/social/bebidastaio'
        page, context, locator = Mock(), Mock(), Mock()
        page.locator.return_value = locator
        locator.filter.return_value = locator
        locator.count.return_value = 2
        with self.assertRaisesRegex(AffiliateError, 'único botão'):
            auto.browser_social_destination(page, context, social)

    def test_browser_social_destination_can_follow_click_navigation(self):
        social = 'https://www.mercadolivre.com.br/social/bebidastaio'
        page, context, locator, control = Mock(), Mock(), Mock(), Mock()
        page.locator.return_value = locator
        locator.filter.return_value = locator
        locator.count.return_value = 1
        locator.first = control
        control.get_attribute.return_value = None
        control.evaluate.return_value = None
        type(page).url = property(lambda self: getattr(self, '_test_url', social))
        page._test_url = social
        context.pages = [page]
        def clicked(*args, **kwargs):
            page._test_url = DIRECT
        control.click.side_effect = clicked
        self.assertEqual(auto.browser_social_destination(page, context, social), DIRECT)

    def test_browser_fallback_fills_data_and_preserves_affiliate_url(self):
        found = auto.extract_product(document(), DIRECT)
        with patch.object(auto, 'fetch_http', side_effect=AffiliateError('HTTP 403')), \
             patch.object(auto, 'fetch_browser', return_value=found) as browser:
            ready = auto.enrich(row())
            self.assertEqual(ready['url'], URL)
            self.assertEqual(ready['price'], '199,90')
            self.assertEqual(ready['api_image'], IMAGE)
            browser.assert_called_once_with(URL)
        self.assertEqual(manual.prepare(ready)['affiliate_url'], URL)

    def test_explicit_operator_price_and_condition_preserved(self):
        found = auto.extract_product(document(), DIRECT)
        with patch.object(auto, 'fetch_http', return_value=found):
            ready = auto.enrich(row('Câmera\nR$ 189,90 no PIX\n' + URL))
            self.assertEqual(ready['price'], '189,90')
            self.assertEqual(ready['price_condition'], 'no PIX')

    def test_untrusted_source_never_fetched(self):
        with patch.object(auto, 'fetch_http') as fetch:
            with self.assertRaises(AffiliateError):
                auto.enrich(dict(row(), chat_id=-123))
            fetch.assert_not_called()

    def test_third_party_ml_offer_may_read_public_metadata_but_preserves_group_price(self):
        entry = dict(kind='ml_offer', source='telegram', chat_id=-123,
                     product_id='MercadoLivre:123456',
                     url='https://www.mercadolivre.com.br/camera/p/MLB123456',
                     price='189,90', price_condition='no PIX', name='')
        found = auto.extract_product(document(), DIRECT)
        with patch.object(auto, 'fetch_http', return_value=found):
            ready = auto.enrich(entry)
        self.assertEqual(ready['price'], '189,90')
        self.assertEqual(ready['price_condition'], 'no PIX')
        self.assertEqual(ready['name'], DATA['name'])
        self.assertEqual(ready['api_image'], IMAGE)

    def test_background_job_does_not_block_and_caches_result(self):
        future = Future(); executor = Mock(); executor.submit.return_value = future
        with patch.object(auto, 'ThreadPoolExecutor', return_value=executor):
            reader = auto.AutoReader()
            entry = row()
            self.assertIsNone(reader.read(entry))
            self.assertIsNone(reader.read(entry))
            ready = dict(entry, **auto.extract_product(document(), DIRECT))
            future.set_result(ready)
            self.assertEqual(reader.read(entry)['price'], '199,90')
            self.assertEqual(reader.read(entry)['price'], '199,90')
            reader.close()
            executor.shutdown.assert_called_once_with(wait=False, cancel_futures=True)

    def test_failed_completed_job_is_removed_and_reported(self):
        future = Future(); future.set_exception(AffiliateError('HTTP 403'))
        executor = Mock(); executor.submit.return_value = future
        with patch.object(auto, 'ThreadPoolExecutor', return_value=executor):
            reader = auto.AutoReader(); entry = row()
            reader.read(entry)
            with self.assertRaisesRegex(AffiliateError, '403'):
                reader.read(entry)
            self.assertEqual(reader.jobs, {})
            reader.close()

    def test_publisher_enriches_before_price_gate_and_publishes_once(self):
        entry = row(); ready = dict(entry, **auto.extract_product(document(), DIRECT))
        reader = Mock(); reader.read.return_value = ready
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(Path(directory) / 'posts.db')
            with patch.object(publisher, 'Ledger', return_value=ledger), \
                 patch.object(publisher.ShopeeAffiliate, 'from_env', side_effect=AffiliateError('ausente')), \
                 patch.object(publisher, 'rows', side_effect=[iter([entry]), iter([entry])]), \
                 patch.object(auto, 'AutoReader', return_value=reader), \
                 patch.object(publisher, 'send', return_value=(42, 0)) as send, \
                 patch.object(publisher.time, 'sleep', side_effect=[None, KeyboardInterrupt]), \
                 patch.dict(os.environ, {'TELEGRAM_TOKEN': 'fake', 'TELEGRAM_CANAL': '@fake', 'EXIGIR_IMAGEM': '1'}), \
                 redirect_stdout(io.StringIO()):
                with self.assertRaises(KeyboardInterrupt):
                    publisher.run_publisher(SimpleNamespace(simular=False), Mock())
                send.assert_called_once()
                self.assertEqual(send.call_args.args[2]['affiliate_url'], URL)
                self.assertEqual(send.call_args.args[3], IMAGE)
                reader.read.assert_called_once()
                reader.close.assert_called_once()
            ledger.db.close()

    def test_user_product_page_format_supported(self):
        url = 'https://www.mercadolivre.com.br/camera/up/MLBU123456'
        result = auto.extract_product(document(dict(DATA, url=url)), url)
        self.assertEqual(result['price'], '199,90')

    def test_pending_read_does_not_block_another_ready_offer(self):
        pending = row(); ready = row('Outra câmera\nR$ 150,00\nhttps://meli.la/Outra')
        ready['api_image'] = IMAGE
        ready['message_id'] = 8
        ready['source_date'] = pending['source_date']
        reader = Mock(); reader.read.return_value = None
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(Path(directory) / 'posts.db')
            with patch.object(publisher, 'Ledger', return_value=ledger), \
                 patch.object(publisher.ShopeeAffiliate, 'from_env', side_effect=AffiliateError('ausente')), \
                 patch.object(publisher, 'rows', return_value=iter([pending, ready])), \
                 patch.object(auto, 'AutoReader', return_value=reader), \
                 patch.object(publisher, 'send', return_value=(42, 0)) as send, \
                 patch.object(publisher.time, 'sleep', side_effect=KeyboardInterrupt), \
                 patch.dict(os.environ, {'TELEGRAM_TOKEN': 'fake', 'TELEGRAM_CANAL': '@fake', 'EXIGIR_IMAGEM': '1'}), \
                 redirect_stdout(io.StringIO()):
                with self.assertRaises(KeyboardInterrupt):
                    publisher.run_publisher(SimpleNamespace(simular=False), Mock())
                send.assert_called_once()
                self.assertEqual(send.call_args.args[2]['url'], ready['url'])
                reader.read.assert_called_once()
            ledger.db.close()

    def test_browser_executes_rendered_page_and_closes_on_success_or_403(self):
        from types import ModuleType
        import sys
        module = ModuleType('playwright.sync_api')
        pw, browser, context, page = Mock(), Mock(), Mock(), Mock()
        session = Mock(); session.__enter__ = Mock(return_value=pw); session.__exit__ = Mock(return_value=False)
        module.sync_playwright = Mock(return_value=session)
        pw.chromium.launch.return_value = browser
        browser.new_context.return_value = context; context.new_page.return_value = page
        page.goto.return_value = SimpleNamespace(status=200)
        page.url = DIRECT; page.content.return_value = document()
        with patch.dict(sys.modules, {'playwright.sync_api': module}):
            self.assertEqual(auto.fetch_browser(URL)['price'], '199,90')
            browser.close.assert_called_once()
            browser.close.reset_mock(); page.goto.return_value = SimpleNamespace(status=403)
            with self.assertRaisesRegex(AffiliateError, '403'):
                auto.fetch_browser(URL)
            browser.close.assert_called_once()
