from tests.publisher_fixtures import persisted_rows
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cupons_shopee as coupons
import bot_ofertas_revisao as publisher
from ofertas_core import Ledger
from shopee_afiliados import ShopeeAffiliate, AffiliateError


def message(text):
    return SimpleNamespace(raw_text=text, get_entities_text=lambda: [], reply_markup=None,
                           id=123, date=datetime.now(timezone.utc))


def alert():
    return coupons.build_alerts([message('Cupons ativos na Shopee\n\nR$ 10 OFF em R$ 119\nOutras categorias selecionadas\nhttps://s.shopee.com.br/teste')], -123)[0]


class CouponTests(unittest.TestCase):
    def test_first_example_multiple_links_no_third_party_promotion(self):
        entries = coupons.coupon_entries([message('🔥 🙏 ALERTA de Cupom Shopee!\n\nLINK LINK: https://s.shopee.com.br/387hsEM3Dd\nLINK LINK: https://s.shopee.com.br/5LCCSDMbNx\n\nConheça nossos grupos no WhatsApp\nhttps://www.cacaprecodorocha.com.br')])
        self.assertEqual(len(entries), 2)
        self.assertTrue(all(not e['conditions'] for e in entries))

    def test_second_example_separates_conditions(self):
        text = ('Cupons ativos na Shopee 🔥\n\nR$ 10 OFF em R$ 119\nOutras categorias selecionadas\n'
                '👉 https://s.shopee.com.br/7Kw5T02I9B\n\nOfertas Relâmpago\n'
                '👉 https://desconto.games/uLWeyUP\n\nR$ 30 OFF em R$ 169 em itens selecionados\n'
                'Confira aqui os itens\n👉 https://desconto.games/GaLsq8T\n\n'
                'Grupos Economizando: https://grupos.economizando.com.br')
        entries = coupons.coupon_entries([message(text)])
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0]['conditions'], 'R$ 10 OFF em R$ 119\nOutras categorias selecionadas')
        self.assertEqual(entries[1]['conditions'], '')
        self.assertEqual(entries[2]['conditions'], 'R$ 30 OFF em R$ 169 em itens selecionados')
        self.assertNotIn('149', coupons.alert_caption(dict(entries=entries)))

    def test_redirector_labeled_as_other_store_is_not_shopee_coupon(self):
        entries = coupons.coupon_entries([message(
            'Cupons ativos na Shopee\n\n'
            'Amazon - Resgate o cupom de 10% OFF no anúncio\n'
            'https://desconto.games/abc123\n\n'
            'R$ 10 OFF em selecionados\n'
            'https://s.shopee.com.br/oficial'
        )])
        self.assertEqual([e['url'] for e in entries], ['https://s.shopee.com.br/oficial'])

    def test_prepare_keeps_valid_entries_when_one_redirector_fails(self):
        raw = alert()
        raw['entries'] = [
            {'url': 'https://desconto.games/bloqueado', 'conditions': ''},
            {'url': 'https://s.shopee.com.br/valido', 'conditions': 'R$ 10 OFF'},
        ]
        client = Mock()
        client.generate_coupon_link.return_value = 'https://s.shopee.com.br/meu'
        def resolve(url):
            if 'desconto.games' in url:
                raise AffiliateError('HTTP 403')
            return 'https://shopee.com.br/m/cupons?voucherCode=OK'
        with patch.object(coupons, 'resolve_coupon', side_effect=resolve):
            ready = coupons.prepare_alert(client, raw)
        self.assertEqual(len(ready['entries']), 1)
        self.assertEqual(ready['entries'][0]['affiliate_url'], 'https://s.shopee.com.br/meu')

    def test_prepare_uses_destination_specific_coupon_affiliate(self):
        client = Mock()
        client.generate_coupon_link.return_value = 'https://s.shopee.com.br/meu'
        with patch.object(
            coupons, 'resolve_coupon',
            return_value='https://shopee.com.br/m/cupons?voucherCode=OK'
        ):
            coupons.prepare_alert(client, alert(), publish_destination='instagram')
        client.generate_coupon_link.assert_called_once_with(
            'https://shopee.com.br/m/cupons?voucherCode=OK',
            publish_destination='instagram',
        )

    def test_product_coupon_code_does_not_turn_product_into_alert(self):
        self.assertEqual(coupons.coupon_entries([message('Notebook\nPor R$ 1000\nCupom: TESTE10\nhttps://s.shopee.com.br/teste')]), [])

    def test_product_and_explicit_coupon_page(self):
        entries = coupons.coupon_entries([message('Notebook\nhttps://shopee.com.br/product/1/2\nResgate cupons\nhttps://s.shopee.com.br/teste')])
        self.assertEqual([e['url'] for e in entries], ['https://s.shopee.com.br/teste'])

    def test_entity_and_button_links(self):
        msg = message('Alerta de cupom Shopee')
        msg.get_entities_text = lambda: [(SimpleNamespace(url='https://s.shopee.com.br/a'), 'resgatar')]
        msg.reply_markup = SimpleNamespace(rows=[SimpleNamespace(buttons=[SimpleNamespace(url='https://s.shopee.com.br/b')])])
        self.assertEqual(len(coupons.coupon_entries([msg])), 2)

    def test_resolver_strips_attribution_preserves_coupon_identity(self):
        response = Mock(status_code=302, headers={'Location': 'https://shopee.com.br/m/cupons?voucherCode=ABC&af_siteid=OTHER&utm_source=old'})
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        transport = Mock()
        transport.get.return_value = response
        self.assertEqual(coupons.resolve_coupon('https://desconto.games/a', transport), 'https://shopee.com.br/m/cupons?voucherCode=ABC')
        self.assertFalse(transport.get.call_args.kwargs['allow_redirects'])

    def test_resolver_blocks_unknown_redirect_and_http_failure(self):
        for code, location in [(302, 'https://evil.test/a'), (403, '')]:
            response = Mock(status_code=code, headers={'Location': location})
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            transport = Mock()
            transport.get.return_value = response
            with self.assertRaises(AffiliateError):
                coupons.resolve_coupon('https://s.shopee.com.br/teste', transport)
            self.assertEqual(transport.get.call_count, 1)

    def test_no_fallback_when_conversion_refused(self):
        client = Mock()
        client.generate_coupon_link.side_effect = AffiliateError('Recusado')
        with patch.object(coupons, 'resolve_coupon', return_value='https://shopee.com.br/m/cupons'):
            with self.assertRaises(AffiliateError):
                coupons.prepare_alert(client, alert())

    def test_api_receives_clean_url_and_subid(self):
        client = ShopeeAffiliate('123', 'secret', 'meucanal')
        with patch.object(client, 'request', return_value={'generateShortLink': {'shortLink': 'https://s.shopee.com.br/novo'}}) as request:
            self.assertEqual(client.generate_coupon_link('https://shopee.com.br/m/cupons?utm_source=other'), 'https://s.shopee.com.br/novo')
            self.assertNotIn('other', request.call_args.args[0])
            self.assertIn('meucanal', request.call_args.args[0])

    def test_api_rejects_original_or_external_return(self):
        client = ShopeeAffiliate('123', 'secret')
        for url in ['https://shopee.com.br/m/cupons', 'https://evil.test/a', None]:
            with patch.object(client, 'request', return_value={'generateShortLink': {'shortLink': url}}):
                with self.assertRaises(AffiliateError):
                    client.generate_coupon_link('https://shopee.com.br/m/cupons')

    def test_identity_uses_resolved_destination_not_shortener(self):
        client = Mock()
        client.generate_coupon_link.return_value = 'https://s.shopee.com.br/novo'
        first, second = alert(), alert()
        second['entries'][0]['url'] = 'https://desconto.games/outro'
        with patch.object(coupons, 'resolve_coupon', return_value='https://shopee.com.br/m/cupons'):
            self.assertEqual(coupons.prepare_alert(client, first)['product_id'], coupons.prepare_alert(client, second)['product_id'])

    def test_send_uses_only_generated_links_and_escapes_conditions(self):
        ready = dict(alert(), affiliate_generated=True)
        ready['entries'] = [dict(conditions='R$ 10 < OFF', affiliate_url='https://s.shopee.com.br/novo')]
        response = Mock()
        response.json.return_value = {'ok': True, 'result': {'message_id': 42}}
        with patch('requests.post', return_value=response) as post:
            self.assertEqual(coupons.send_alert('fake', '@fake', ready), (42, 0))
            data = post.call_args.kwargs['data']
            self.assertIn('&lt;', data['text'])
            self.assertIn('🔥 <b>Cupom Shopee</b>', data['text'])
            self.assertIn('#anuncio', data['text'])
            self.assertNotIn('Resgate aqui:', data['text'])
            self.assertNotIn('Confira os cupons disponíveis.', data['text'])
            self.assertIn('<b>🎟️ Opção 1</b>\nhttps://s.shopee.com.br/novo', data['text'])
            self.assertIn('🏷️ R$ 10 &lt; OFF', data['text'])
            self.assertNotIn('reply_markup', data)

    def test_caption_places_each_link_directly_under_its_option(self):
        ready = {
            'entries': [
                {'conditions': '', 'affiliate_url': 'https://s.shopee.com.br/9KiRALsiZB'},
                {'conditions': '', 'affiliate_url': 'https://s.shopee.com.br/gQSqQUFRk'},
            ]
        }
        rendered = coupons.alert_caption(ready)
        expected = (
            '🔥 <b>Cupom Shopee</b>\n\n'
            '<b>🎟️ Opção 1</b>\n'
            'https://s.shopee.com.br/9KiRALsiZB\n\n'
            '<b>🎟️ Opção 2</b>\n'
            'https://s.shopee.com.br/gQSqQUFRk\n\n'
            'Confira validade, disponibilidade e regras de cada cupom na Shopee.\n\n'
            '#anuncio'
        )
        self.assertEqual(rendered, expected)
        self.assertNotIn('Resgate aqui:', rendered)
        self.assertNotIn('Confira os cupons disponíveis.', rendered)

    def test_caption_supports_value_percent_limit_and_other_explicit_conditions(self):
        ready = {
            'entries': [
                {'conditions': 'R$ 25 OFF a partir de R$ 199',
                 'affiliate_url': 'https://s.shopee.com.br/a'},
                {'conditions': '10% OFF\nLimite de R$ 11 OFF',
                 'affiliate_url': 'https://s.shopee.com.br/b'},
                {'conditions': 'Frete grátis em itens selecionados\nVálido até 23h',
                 'affiliate_url': 'https://s.shopee.com.br/c'},
            ]
        }
        rendered = coupons.alert_caption(ready)
        self.assertIn('🏷️ R$ 25 OFF a partir de R$ 199\n<b>🎟️ Opção 1</b>\nhttps://s.shopee.com.br/a', rendered)
        self.assertIn('🏷️ 10% OFF\nLimite de R$ 11 OFF\n<b>🎟️ Opção 2</b>\nhttps://s.shopee.com.br/b', rendered)
        self.assertIn('🏷️ Frete grátis em itens selecionados\nVálido até 23h\n<b>🎟️ Opção 3</b>\nhttps://s.shopee.com.br/c', rendered)

    def test_condition_text_deduplicates_cleans_markdown_and_rejects_promotional_noise(self):
        value = ('**🏷️ R$ 30 OFF acima de R$ 169**\n'
                 'R$ 30 OFF acima de R$ 169\n'
                 'Entre no nosso grupo Telegram\n'
                 'Itens selecionados')
        rendered = coupons.condition_text(value)
        self.assertEqual(rendered, '🏷️ R$ 30 OFF acima de R$ 169\nItens selecionados')

    def test_no_send_without_generated_flag(self):
        with patch('requests.post') as post:
            with self.assertRaises(AffiliateError):
                coupons.send_alert('fake', '@fake', alert())
            post.assert_not_called()

    def test_long_caption_sends_full_text_instead_of_truncating(self):
        ready = dict(alert(), affiliate_generated=True, entries=[dict(conditions='R$ 10 OFF ' * 130, affiliate_url='https://s.shopee.com.br/novo')])
        response = Mock()
        response.json.return_value = {'ok': True, 'result': {'message_id': 42}}
        with patch('requests.post', return_value=response) as post:
            coupons.send_alert('fake', '@fake', ready, Mock())
            self.assertTrue(post.call_args.args[0].endswith('sendMessage'))
            normalized = coupons.condition_text(ready['entries'][0]['conditions'])
            self.assertIn(normalized, post.call_args.kwargs['data']['text'])

    def test_coupon_publishes_during_radar_wait_and_deduplicates(self):
        raw = alert()
        ready = dict(
            raw,
            affiliate_generated=True,
            entries=[
                dict(entry, affiliate_url=f'https://s.shopee.com.br/gate{index}')
                for index, entry in enumerate(raw['entries'], 1)
            ],
        )
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(Path(directory) / 'publicacoes.sqlite3')
            ledger.mark_attempt(600, clock_id=2)
            with patch.object(publisher, 'BASE', Path(directory)), \
                 patch.object(publisher, 'Ledger', return_value=ledger), \
                 patch.object(publisher.ShopeeAffiliate, 'from_env'), \
                 patch.object(publisher, 'rows', side_effect=[persisted_rows([raw], directory), persisted_rows([raw], directory)]), \
                 patch.object(publisher, 'prepare_alert', return_value=ready) as prepare, \
                 patch.object(publisher, 'send_alert', return_value=(42, 0)) as send, \
                 patch.object(publisher, 'banner_path', return_value=None), \
                 patch.object(publisher.time, 'sleep', side_effect=[None, KeyboardInterrupt]), \
                 patch.dict('os.environ', {'TELEGRAM_TOKEN': 'fake', 'TELEGRAM_CANAL': '@fake'}):
                with self.assertRaises(KeyboardInterrupt):
                    publisher.run_publisher(SimpleNamespace(simular=False), Mock())
                self.assertEqual(send.call_count, 1)
                self.assertEqual(prepare.call_count, 1)
                self.assertGreater(ledger.publication_delay(clock_id=2), 590)
            ledger.db.close()


if __name__ == '__main__':
    unittest.main()
