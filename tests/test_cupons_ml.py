import io
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cupons_mercadolivre as ml
import bot_ofertas_revisao as publisher
from ofertas_core import Ledger
from shopee_afiliados import AffiliateError

VALUES = [('HOJETEMPROMO', 10, 149, 200), ('MELIGARANTE', 30, 1, 500),
          ('MELISALVA', 30, 1, 500), ('MELICOMPRA', 30, 1, 500),
          ('MLVANTAGENS', 25, 1, 500), ('COMPRACERTA', 20, 19, 100),
          ('ECONOMIATOTAL', 20, 79, 60), ('MIMODODIA', 8, 150, 300),
          ('BARATINHO', 15, 50, 200), ('TODEBOA', 10, 99, 300),
          ('MELIDATADUPLA', 10, 99, 300), ('CUPOMLIBERADO', 18, 49, 50),
          ('MLMELHOROFERTA', 18, 79, 60), ('DIADACRIANCA', 15, 59, 50)]
FIRST = 'MERCADO LIVRE\n&#xA;**&#x20;em Selecionados!**\n\u200b\n' + '\n'.join(
    f'🏷 {pct}% OFF acima de R$ {minimum}, limite R$ {maximum}: `{code}`' for code, pct, minimum, maximum in VALUES
) + '\n[https://meli.la/2LGgj3U](https://meli.la/2LGgj3U)'
SECOND = '🔥**NOVO CUPOM NO MERCADO LIVRE&#x20;**\n' + '\n\n'.join(
    f'✅ {pct}% OFF em R${minimum}, Limite de R$ {maximum} OFF: ` {code}`' for code, pct, minimum, maximum in VALUES)


def build(text=FIRST):
    return ml.build_alert([SimpleNamespace(raw_text=text, date=datetime.now(timezone.utc), id=1)], -123)


class MLCouponTests(unittest.TestCase):
    def test_both_full_examples_keep_all_codes_and_conditions(self):
        for text in [FIRST, SECOND]:
            with self.subTest(text=text[:20]):
                row = ml.prepare_alert(build(text))
                self.assertEqual([i['code'] for i in row['entries']], [v[0] for v in VALUES])
                rendered = ml.alert_caption(row)
                self.assertIn('🔥 <b>Cupom Mercado Livre</b>', rendered)
                self.assertIn('#anuncio', rendered)
                self.assertIn('Resgate aqui:', rendered)
                self.assertEqual(rendered.count(ml.DEFAULT_SOCIAL_URL), 1)
                for code, pct, minimum, maximum in VALUES:
                    self.assertIn('<code>' + code + '</code>', rendered)
                self.assertNotIn('meli.la', rendered)
                self.assertNotIn('&#x', rendered)
        self.assertIn('em Selecionados!', ml.alert_caption(build()))
        self.assertIn('10% OFF acima de R$ 149, limite R$ 200', ml.alert_caption(build()))

    def test_removes_links_buttons_promos_and_retains_extra_conditions(self):
        row = build(FIRST + '\nVálido até 23h, somente primeira compra.\n'
                    'Entre no nosso grupo https://t.me/terceiro\n'
                    'Compre aqui: [abrir](https://meli.la/afiliado)\n'
                    'www.outra-loja.com.br\n@terceiro\n'
                    '<a href="https://outro.com/compra">cupom</a>')
        rendered = ml.alert_caption(row)
        self.assertNotRegex(rendered.replace(ml.DEFAULT_SOCIAL_URL, ''), r'https?://|www\.|meli\.la|t\.me|@terceiro|href=')
        self.assertEqual(rendered.count(ml.DEFAULT_SOCIAL_URL), 1)
        self.assertIn('somente primeira compra', rendered)

    def test_non_ml_and_non_coupon_are_not_classified(self):
        self.assertIsNone(build('Shopee\n10% OFF: TESTECUPOM'))
        self.assertIsNone(build('Mercado Livre monitor R$ 149 https://meli.la/abc'))
        self.assertIsNone(build('Monitor AOC (Mercado Livre)\n10% OFF: TESTECUPOM\nR$ 149'))

    def test_long_list_one_request_and_no_truncation(self):
        row = build(SECOND + '\nCondição adicional: ' + 'A' * 250)
        response = Mock(); response.json.return_value = {'ok': True, 'result': {'message_id': 99}}
        with patch('requests.post', return_value=response) as post:
            self.assertEqual(ml.send_alert('fake', '@fake', row, Mock()), (99, 0))
            post.assert_called_once()
            self.assertTrue(post.call_args.args[0].endswith('/sendMessage'))
            data = post.call_args.kwargs['data']
            self.assertNotIn('reply_markup', data)
            self.assertEqual(data['text'].count('<code>'), 14)
            self.assertIn('A' * 250, data['text'])

    def test_examples_fit_one_photo_with_all_fourteen_codes(self):
        for text in [FIRST, SECOND]:
            row = build(text)
            self.assertLessEqual(ml.visible_length(ml.alert_caption(row)), 1024)
            with tempfile.TemporaryDirectory() as directory:
                photo = Path(directory) / 'banner.png'; photo.write_bytes(b'fake')
                response = Mock(); response.json.return_value = {'ok': True, 'result': {'message_id': 9}}
                with patch('requests.post', return_value=response) as post:
                    ml.send_alert('fake', '@fake', row, photo)
                    post.assert_called_once()
                    self.assertTrue(post.call_args.args[0].endswith('/sendPhoto'))
                    self.assertEqual(post.call_args.kwargs['data']['caption'].count('<code>'), 14)

    def test_short_list_single_photo_and_counts_parsed_html(self):
        row = build('Mercado Livre\n10% OFF acima de R$ 149: TESTECUPOM\n' + 'A'*850)
        self.assertLessEqual(ml.visible_length(ml.alert_caption(row)), 1024)
        with tempfile.TemporaryDirectory() as directory:
            photo = Path(directory) / 'banner.png'; photo.write_bytes(b'fake')
            response = Mock(); response.json.return_value = {'ok': True, 'result': {'message_id': 9}}
            with patch('requests.post', return_value=response) as post:
                ml.send_alert('fake', '@fake', row, photo)
                post.assert_called_once()
                self.assertTrue(post.call_args.args[0].endswith('/sendPhoto'))
                self.assertIn('caption', post.call_args.kwargs['data'])

    def test_over_limit_blocked_without_sending_partial_list(self):
        row = build(FIRST + '\n' + 'A'*4096)
        with patch('requests.post') as post:
            with self.assertRaisesRegex(AffiliateError, '4096'):
                ml.send_alert('fake', '@fake', row)
            post.assert_not_called()

    def test_identity_ignores_origin_link_and_line_order_not_conditions(self):
        row = build()
        changed_link = build(FIRST.replace('2LGgj3U', 'other'))
        changed_value = build(FIRST.replace('limite R$ 200', 'limite R$ 100'))
        self.assertEqual(row['product_id'], changed_link['product_id'])
        self.assertNotEqual(row['product_id'], changed_value['product_id'])
        self.assertEqual(row['product_id'], build('MERCADO LIVRE\n' + '\n'.join(reversed(FIRST.splitlines()[1:])))['product_id'])

    def test_publisher_sends_once_without_shopee_credentials_or_affiliate_links(self):
        row = build()
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(Path(directory) / 'posts.db')
            with patch.object(publisher, 'Ledger', return_value=ledger), \
                 patch.object(publisher.ShopeeAffiliate, 'from_env', side_effect=AffiliateError('ausente')), \
                 patch.object(publisher, 'rows', side_effect=[iter([row]), iter([row])]), \
                 patch.object(ml, 'send_alert', return_value=(42, 0)) as send, \
                 patch.object(ml, 'banner_path', return_value=None), \
                 patch.object(publisher.time, 'sleep', side_effect=[None, KeyboardInterrupt]), \
                 patch.dict('os.environ', {'TELEGRAM_TOKEN': 'fake', 'TELEGRAM_CANAL': '@fake'}), \
                 redirect_stdout(io.StringIO()):
                with self.assertRaises(KeyboardInterrupt):
                    publisher.run_publisher(SimpleNamespace(simular=False), Mock())
                send.assert_called_once()
            ledger.db.close()

    def test_retry_and_uncertain_response_are_not_duplicate_sends(self):
        response = Mock(); response.json.return_value = {'ok': False, 'parameters': {'retry_after': 90}}
        with patch('requests.post', return_value=response) as post:
            self.assertEqual(ml.send_alert('fake', '@fake', build()), (None, 90))
            post.assert_called_once()
        response.json.side_effect = ValueError('unexpected')
        with patch('requests.post', return_value=response) as post:
            with self.assertRaises(ValueError):
                ml.send_alert('fake', '@fake', build())
            post.assert_called_once()
