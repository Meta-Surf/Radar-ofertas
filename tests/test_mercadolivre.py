"""Mercado Livre: somente mocks; não usa tokens, arquivos ou APIs reais."""
import importlib
import unittest
from unittest.mock import Mock, patch

import requests
import radar_mercadolivre_v6 as ml
import teste_mercadolivre as manual


def response(status, payload):
    return Mock(status_code=status, json=Mock(return_value=payload), text=str(payload))


class MercadoLivreTests(unittest.TestCase):
    def setUp(self):
        self.network = patch.object(ml, 'SESSION').start()
        self.addCleanup(patch.stopall)
        patch.object(ml, 'log').start()

    def test_auth_header(self):
        with patch.object(ml, 'ML_ACCESS_TOKEN', 'token-ficticio'):
            ml.ml_request('GET', '/users/me')
        self.assertEqual(self.network.request.call_args.kwargs['headers']['Authorization'],
                         'Bearer token-ficticio')

    def test_401_renews_only_once(self):
        self.network.request.return_value = response(401, {})
        with patch.object(ml, 'renovar_token', return_value=True) as renew:
            self.assertEqual(ml.ml_request('GET', '/users/me').status_code, 401)
        renew.assert_called_once()
        self.assertEqual(self.network.request.call_count, 2)

    def test_failed_refresh_does_not_retry(self):
        self.network.request.return_value = response(401, {})
        with patch.object(ml, 'renovar_token', return_value=False):
            ml.ml_request('GET', '/users/me')
        self.network.request.assert_called_once()

    def test_refresh_updates_tokens_without_real_file(self):
        self.network.post.return_value = response(200, {'access_token': 'novo', 'refresh_token': 'refresh-novo'})
        with patch.object(ml, 'atualizar_env') as save, patch.object(ml, 'ML_ACCESS_TOKEN', 'antigo'), patch.object(ml, 'ML_REFRESH_TOKEN', 'antigo'):
            self.assertTrue(ml.renovar_token())
            save.assert_called_once_with({'ML_ACCESS_TOKEN': 'novo', 'ML_REFRESH_TOKEN': 'refresh-novo'})
            self.assertEqual(ml.ML_ACCESS_TOKEN, 'novo')

    def test_403_does_not_renew_or_guess_cause(self):
        self.network.request.return_value = response(403, {'error': 'access_denied'})
        with patch.object(ml, 'renovar_token') as renew:
            self.assertIsNone(ml.obter_item('MLB123456789'))
        renew.assert_not_called()
        self.assertTrue(any('causa não determinada' in str(c) for c in ml.log.call_args_list))

    def test_individual_403(self):
        self.network.request.return_value = response(200, [{'code': 403, 'body': {'error': 'access_denied'}}])
        self.assertIsNone(ml.obter_item('MLB123456789'))
        self.assertTrue(any('acesso_negado_403' in str(c) for c in ml.log.call_args_list))

    def test_bulk_success(self):
        item = {'id': 'MLB123456789', 'price': 10}
        self.network.request.return_value = response(200, [{'code': 200, 'body': item}])
        self.assertEqual(ml.obter_item(item['id']), item)

    def test_invalid_json(self):
        self.network.request.return_value = Mock(status_code=200, json=Mock(side_effect=ValueError))
        self.assertIsNone(ml.obter_item('MLB123456789'))

    def test_network_failure(self):
        self.network.request.side_effect = requests.Timeout
        self.assertIsNone(ml.obter_item('MLB123456789'))

    def test_monitor_requires_no_telegram_credentials(self):
        with patch.multiple(ml, TELEGRAM_TOKEN='', TELEGRAM_CANAL='', ML_CLIENT_ID='x',
                            ML_CLIENT_SECRET='x', ML_ACCESS_TOKEN='x', ML_REFRESH_TOKEN='x'):
            ml.validar_configuracao()

    def test_direct_send_always_blocked_even_if_telegram_would_timeout(self):
        self.network.post.side_effect = requests.Timeout
        for affiliate in (True, False):
            with self.subTest(affiliate=affiliate):
                self.assertFalse(ml.enviar_telegram({'affiliate': affiliate, 'link': 'https://meli.la/exemplo'}))
        self.network.post.assert_not_called()

    def test_cycle_with_or_without_affiliate_is_read_only(self):
        item = {'id': 'MLB123456789', 'status': 'active', 'price': 50, 'original_price': 100,
                'permalink': 'https://www.mercadolivre.com.br/p/MLB123456789',
                'title': 'Produto teste', 'pictures': [{'secure_url': 'https://example.com/foto.jpg'}]}
        for mapped in ({}, {item['id']: 'https://meli.la/exemplo'}):
            for dry_run in (True, False):
                with self.subTest(mapped=bool(mapped), dry_run=dry_run), patch.object(ml, 'obter_item', return_value=item), patch.object(ml, 'carregar_links_afiliados', return_value=mapped), patch.object(ml, 'salvar_estado') as save, patch.object(ml, 'enviar_telegram') as send, patch('builtins.print') as output, patch.object(ml, 'ML_MIN_DESCONTO', 5):
                    ml.ciclo(dry_run=dry_run, item_unico=item['id'])
                    send.assert_not_called()
                    save.assert_not_called()
                    self.assertIn('mapeado' if mapped else 'sem afiliação', str(output.call_args_list))
        self.network.post.assert_not_called()

    def test_manual_script_import_has_no_network(self):
        with patch.object(requests, 'get') as get:
            importlib.reload(manual)
        get.assert_not_called()

    def test_manual_script_blocked_in_ci(self):
        with patch.dict('os.environ', {'CI': 'true'}), patch.object(requests, 'get') as get:
            with self.assertRaises(SystemExit):
                manual.main()
        get.assert_not_called()
