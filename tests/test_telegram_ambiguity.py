"""Respostas incompletas nunca autorizam reenvio; rede e bancos locais."""
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
import bot_ofertas_revisao as publisher
from fila_ofertas_sqlite import CapturedOfferQueue
from ofertas_core import Ledger
from telegram_api import TelegramSendError, is_media_error, response_message_id, send_telegram


def response(body, status=200):
    return SimpleNamespace(status_code=status, json=lambda: body)


class TelegramEnvelopeTests(unittest.TestCase):
    def assert_uncertain(self, reply):
        with self.assertRaises(TelegramSendError) as caught:
            response_message_id(reply)
        error = caught.exception
        self.assertEqual(error.kind, 'uncertain')
        self.assertFalse(error.discard)
        self.assertFalse(error.global_pause)
        self.assertEqual(error.retry_after, 0)
        self.assertFalse(is_media_error(error))

    def test_missing_ok_is_uncertain_for_success_and_all_error_shapes(self):
        for status, body in (
            (200, {'result': {'message_id': 123}}),
            (400, {'error_code': 400, 'description': 'invalid photo'}),
            (429, {'error_code': 429, 'parameters': {'retry_after': 19}}),
            (500, {'error_code': 500}), (502, {'error_code': 502}),
            (200, {}), (200, []), (200, None), (200, 'invalid'),
        ):
            with self.subTest(status=status, body=body):
                self.assert_uncertain(response(body, status))

    def test_ok_must_be_boolean_not_truthy_or_equal_to_boolean(self):
        for value in (None, 0, 1, '', 'true', 'false', [], {}):
            with self.subTest(ok=value):
                self.assert_uncertain(response({'ok': value, 'error_code': 400,
                    'description': 'invalid photo', 'result': {'message_id': 77}}, 400))

    def test_success_requires_valid_native_message_id(self):
        self.assertEqual(response_message_id(response({'ok': True,
            'result': {'message_id': 77}})), 77)
        for result in (None, [], 'bad', {}, {'message_id': None}, {'message_id': 0},
                       {'message_id': -1}, {'message_id': True}, {'message_id': 77.5}):
            with self.subTest(result=result):
                self.assert_uncertain(response({'ok': True, 'result': result}))

    def test_explicit_rejections_keep_existing_policy(self):
        for code, kind in ((400, 'permanent'), (429, 'rate_limit'),
                           (401, 'configuration'), (403, 'configuration'),
                           (404, 'configuration'), (500, 'transient'), (502, 'transient')):
            with self.subTest(code=code):
                with self.assertRaises(TelegramSendError) as caught:
                    response_message_id(response({'ok': False, 'error_code': code,
                        'description': 'invalid text', 'parameters': {'retry_after': 19}}, code))
                self.assertEqual(caught.exception.kind, kind)
                if code == 429:
                    self.assertEqual(caught.exception.retry_after, 19)

    def test_ambiguous_media_response_never_falls_back(self):
        for ok in ('missing', None, 0, 1, 'false'):
            with self.subTest(ok=ok):
                body = {'error_code': 400, 'description': 'invalid photo'}
                if ok != 'missing':
                    body['ok'] = ok
                client = Mock()
                client.post.return_value = response(body, 400)
                with self.assertRaises(TelegramSendError) as caught:
                    send_telegram(client, 'fake', {'chat_id': '@audit', 'caption': 'local'},
                                  image='https://fixture.invalid/image.jpg')
                self.assertEqual(caught.exception.kind, 'uncertain')
                self.assertEqual(client.post.call_count, 1)

    def test_network_and_invalid_json_never_fall_back(self):
        for failure in (requests.Timeout, requests.ConnectionError, ValueError):
            with self.subTest(failure=failure):
                client = Mock()
                if failure is ValueError:
                    client.post.return_value.status_code = 200
                    client.post.return_value.json.side_effect = failure('local invalid JSON')
                else:
                    client.post.side_effect = failure('local network failure')
                with self.assertRaises(TelegramSendError) as caught:
                    send_telegram(client, 'fake', {'chat_id': '@audit', 'caption': 'local'},
                                  image='https://fixture.invalid/image.jpg')
                self.assertEqual(caught.exception.kind, 'uncertain')
                self.assertEqual(client.post.call_count, 1)


class PublisherAmbiguityTests(unittest.TestCase):
    def test_real_publisher_preserves_queue_and_blocks_after_one_simulated_acceptance(self):
        # Exercita run_publisher e send_offer reais, interceptando toda a rede.
        for status, body in ((502, {'error_code': 502}),
                             (400, {'error_code': 400, 'description': 'invalid photo'})):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                ledger = Ledger(root / 'publicacoes.sqlite3')
                self.addCleanup(ledger.db.close)
                offer = dict(product_id='Shopee:1:11', store='Shopee',
                    url='https://shopee.com.br/product/1/11', price='99,90',
                    source='telegram', source_date=datetime.now(timezone.utc).isoformat(),
                    chat_id=-1, message_id=11, api_image='https://fixture.invalid/image.jpg')
                queue = CapturedOfferQueue(root / 'publicacoes.sqlite3')
                queue.replace_capture([offer])
                queue.close()
                client = Mock()
                client.prepare.side_effect = lambda o: dict(o,
                    affiliate_url='https://s.shopee.com.br/gateok',
                    affiliate_generated=True, price_from=False)
                accepted = []
                def fake_post(*args, **kwargs):
                    accepted.append(77 + len(accepted))
                    if len(accepted) == 1:
                        return response(body, status)
                    return response({'ok': True, 'result': {'message_id': accepted[-1]}})
                gate = Mock()
                gate.validate.side_effect = lambda original, prepared, channel, *, selected=None: prepared
                metrics = Mock()
                shadow = Mock()
                with ExitStack() as stack:
                    stack.enter_context(patch.object(publisher, 'BASE', root))
                    stack.enter_context(patch.object(publisher, 'Ledger', return_value=ledger))
                    stack.enter_context(patch.object(publisher.ShopeeAffiliate, 'from_env', return_value=client))
                    for factory in (publisher.MercadoLivreAffiliate, publisher.KabumAffiliate, publisher.AmazonCreators):
                        stack.enter_context(patch.object(factory, 'from_env', side_effect=publisher.AffiliateError('disabled locally')))
                    stack.enter_context(patch('mercadolivre_auto.AutoReader', return_value=Mock()))
                    stack.enter_context(patch.object(publisher, 'PrePublicationGate', return_value=gate))
                    stack.enter_context(patch.object(publisher, 'SourceMetrics', return_value=metrics))
                    stack.enter_context(patch.object(publisher, 'ShadowDistribution', return_value=shadow))
                    network = stack.enter_context(patch('requests.post', side_effect=fake_post))
                    stack.enter_context(patch('requests.get', side_effect=AssertionError('unexpected network')))
                    release = stack.enter_context(patch.object(ledger, 'release', wraps=ledger.release))
                    uncertain = stack.enter_context(patch.object(ledger, 'mark_uncertain', wraps=ledger.mark_uncertain))
                    failure = stack.enter_context(patch.object(publisher.PublisherBackoff, 'failure'))
                    discard = stack.enter_context(patch.object(publisher.CapturedOfferQueue, 'discard_product'))
                    stack.enter_context(patch.object(publisher.time, 'sleep', side_effect=KeyboardInterrupt))
                    stack.enter_context(patch.dict('os.environ', {'TELEGRAM_TOKEN': 'fake', 'TELEGRAM_CANAL': '@audit'}))
                    stack.enter_context(patch('builtins.print'))
                    with self.assertRaises(KeyboardInterrupt):
                        publisher.run_publisher(SimpleNamespace(simular=False), Mock())
                self.assertEqual(accepted, [77])
                self.assertEqual(network.call_count, 1)
                uncertain.assert_called_once()
                release.assert_not_called()
                failure.assert_not_called()
                discard.assert_not_called()
                shadow.mirror_success.assert_not_called()
                self.assertFalse(any(call.args[1] == 'PUBLICADA' for call in metrics.record_offer.call_args_list))
                self.assertEqual(ledger.db.execute('SELECT status FROM posts').fetchall(), [('uncertain',)])
                self.assertEqual(ledger.db.execute('SELECT state FROM deliveries').fetchall(), [('UNCERTAIN',)])
                self.assertEqual(ledger.db.execute('SELECT count(*) FROM captured_queue').fetchone()[0], 1)
                self.assertEqual(ledger.db.execute('SELECT count(*) FROM publisher_retry').fetchone()[0], 0)
                self.assertIsNone(ledger.reserve(offer['product_id'], offer=offer, channel='@audit'))
                self.assertEqual(ledger.deliveries.ready('telegram'), [])

    def test_ambiguous_delivery_blocks_eight_connections_reserve_and_claim_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'local.sqlite3'
            ledger = Ledger(path)
            for n in range(12):
                key = f'AMBIGUOUS:{n}'
                day = ledger.reserve(key)
                ledger.mark_sending(key, day)
                client = Mock()
                client.post.return_value = response({'error_code': 502}, 502)
                with self.assertRaises(TelegramSendError) as caught:
                    send_telegram(client, 'fake', {'chat_id': '@audit', 'text': 'local'})
                self.assertEqual(caught.exception.kind, 'uncertain')
                ledger.mark_uncertain(key, day, channel='@audit')
                self.assertEqual(client.post.call_count, 1)
            ids = [row[0] for row in ledger.db.execute('SELECT id FROM deliveries ORDER BY id')]
            ledger.db.close()
            barrier = threading.Barrier(8)
            def worker(_):
                other = Ledger(path)
                try:
                    barrier.wait()
                    reservations = claims = 0
                    for n in range(128):
                        reservations += other.reserve(f'AMBIGUOUS:{n % 12}') is not None
                        claims += other.deliveries.claim(ids[n % 12])
                    return reservations, claims, other.deliveries.ready('telegram')
                finally:
                    other.db.close()
            with ThreadPoolExecutor(max_workers=8) as pool:
                self.assertEqual(list(pool.map(worker, range(8))), [(0, 0, [])] * 8)
            ledger = Ledger(path)
            try:
                self.assertEqual(ledger.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual(ledger.db.execute("SELECT count(*) FROM posts WHERE status='uncertain'").fetchone()[0], 12)
            finally:
                ledger.db.close()

    def test_sent_cannot_regress_to_uncertain_or_be_released(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(Path(directory) / 'local.sqlite3')
            try:
                day = ledger.reserve('SENT:1')
                ledger.mark_sending('SENT:1', day)
                ledger.finish('SENT:1', day, 77, channel='@audit')
                self.assertFalse(ledger.mark_uncertain('SENT:1', day, channel='@audit'))
                ledger.release('SENT:1', day)
                self.assertEqual(ledger.db.execute('SELECT status,message_id FROM posts').fetchone(), ('sent', 77))
                self.assertEqual(ledger.db.execute('SELECT state,external_id FROM deliveries').fetchone(), ('SENT', '77'))
            finally:
                ledger.db.close()
