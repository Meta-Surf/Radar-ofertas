import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import mercadolivre_manual as ml
import cupons_mercadolivre as coupons
import bot_ofertas_revisao as publisher
from ofertas_core import Ledger, caption
from shopee_afiliados import AffiliateError

CHAT = -1003988174916
URL = 'https://meli.la/MeuLink'
TEXT = 'Monitor AOC 27 polegadas\nPor: R$ 1.103,08 no PIX\n' + URL


def messages(text=TEXT):
    return [SimpleNamespace(raw_text=text, id=5, date=datetime.now(timezone.utc),
                            get_entities_text=lambda: [], reply_markup=None)]


class ManualMLTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'ML_MANUAL_CHAT': str(CHAT)})
        self.env.start(); self.addCleanup(self.env.stop)

    def test_captures_and_preserves_exact_link_price_and_condition_without_network(self):
        with patch('requests.get') as get:
            raw = ml.build_offer(messages(), CHAT)
            ready = ml.prepare(raw)
            self.assertEqual(ready['affiliate_url'], URL)
            self.assertFalse(ready['affiliate_generated'])
            self.assertEqual(ready['price'], '1.103,08')
            self.assertEqual(ready['price_condition'], 'no PIX')
            self.assertIn('MERCADO LIVRE', caption(ready))
            get.assert_not_called()
        raw['url'] = 'https://www.mercadolivre.com.br/p/MLB123456?matt_tool=123&matt_word=meu'
        raw['product_id'] = ml.key(raw['url'])
        self.assertEqual(ml.prepare(raw)['affiliate_url'], raw['url'])

    def test_only_authorized_group_can_preserve_links(self):
        self.assertIsNone(ml.build_offer(messages(), -123))
        raw = ml.build_offer(messages(), CHAT)
        for changes in [{'chat_id': -123}, {'source': 'shopee_api'}, {'product_id': 'MLManual:fake'}]:
            with self.subTest(changes=changes), self.assertRaises(AffiliateError):
                ml.prepare(dict(raw, **changes))
        with patch.dict(os.environ, {'ML_MANUAL_CHAT': ''}):
            self.assertFalse(ml.trusted(CHAT))

    def test_only_ml_https_links_with_exact_hosts(self):
        for bad in ['http://meli.la/x', 'https://meli.la.evil.test/x', 'https://meli.la@evil.test/x',
                    'https://evil.test/x', 'https://meli.la:444/x', 'https://meli.la/',
                    'https://meli.la/x\n', 'https://meli.la\\@evil.test/x']:
            self.assertFalse(ml.allowed_link(bad), bad)

    def test_repeated_same_link_accepted_but_multiple_products_blocked(self):
        self.assertEqual(ml.build_offer(messages(TEXT + '\n' + URL), CHAT)['url'], URL)
        with self.assertRaises(AffiliateError):
            ml.build_offer(messages(TEXT + '\nhttps://meli.la/OutroProduto'), CHAT)

    def test_missing_or_ambiguous_price_waits_for_source_edit(self):
        for text in ['Monitor AOC\n' + URL, TEXT + '\nR$ 999,00']:
            raw = ml.build_offer(messages(text), CHAT)
            self.assertIsNone(raw['price'])
            with self.assertRaises(AffiliateError):
                ml.prepare(raw)

    def test_sender_keeps_exact_button_link_and_rejects_untrusted_queue(self):
        raw = ml.build_offer(messages(), CHAT)
        response = Mock(); response.json.return_value = {'ok': True, 'result': {'message_id': 42}}
        with patch('requests.post', return_value=response) as post:
            publisher.send('fake', '@fake', raw, None)
            data = post.call_args.kwargs['data']
            self.assertEqual(json.loads(data['reply_markup'])['inline_keyboard'][0][0]['url'], URL)
            self.assertIn('<b>R$ 1.103,08</b>', data['text'])
            post.reset_mock()
            with self.assertRaises(AffiliateError):
                publisher.send('fake', '@fake', dict(raw, chat_id=-123), None)
            post.assert_not_called()

    def test_coupons_use_fixed_social_in_text_even_from_manual_group(self):
        text = 'NOVOS CUPONS\n10% OFF acima de R$ 149, limite R$ 200: HOJETEMPROMO\n' + URL
        manual = ml.build_coupon(messages(text), CHAT)
        self.assertEqual(manual['manual_links'], [URL])
        self.assertEqual(coupons.prepare_alert(manual)['entries'][0]['code'], 'HOJETEMPROMO')
        regular = ml.build_coupon(messages('MERCADO LIVRE\n' + text), -123)
        self.assertNotIn('manual_links', regular)
        response = Mock(); response.json.return_value = {'ok': True, 'result': {'message_id': 5}}
        with patch('requests.post', return_value=response) as post:
            coupons.send_alert('fake', '@fake', manual)
            self.assertEqual(post.call_count, 1)
            data = post.call_args.kwargs['data']
            self.assertNotIn('reply_markup', data)
            self.assertIn(coupons.DEFAULT_SOCIAL_URL, data['text'])
            self.assertNotIn(URL, data['text'])
            coupons.send_alert('fake', '@fake', dict(manual, chat_id=-123))
            self.assertNotIn('reply_markup', post.call_args.kwargs['data'])

    def test_manual_publisher_works_without_shopee_and_sends_only_once(self):
        row = ml.build_offer(messages(), CHAT)
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(Path(directory) / 'posts.db')
            ledger.mark_attempt(600, clock_id=2)
            with patch.object(publisher, 'Ledger', return_value=ledger), \
                 patch.object(publisher.ShopeeAffiliate, 'from_env', side_effect=AffiliateError('ausente')), \
                 patch.object(publisher, 'rows', side_effect=[iter([row]), iter([row])]), \
                 patch.object(publisher, 'send', return_value=(42, 0)) as send, \
                 patch.object(publisher.time, 'sleep', side_effect=[None, KeyboardInterrupt]), \
                 patch.dict(os.environ, {'TELEGRAM_TOKEN': 'fake', 'TELEGRAM_CANAL': '@fake', 'EXIGIR_IMAGEM': '0'}), \
                 redirect_stdout(io.StringIO()):
                with self.assertRaises(KeyboardInterrupt):
                    publisher.run_publisher(SimpleNamespace(simular=False), Mock())
                send.assert_called_once()
                self.assertEqual(send.call_args.args[2]['affiliate_url'], URL)
                self.assertGreater(ledger.publication_delay(clock_id=2), 590)
            ledger.db.close()

    def test_actual_monitor_adds_group_captures_media_and_queues_edits_without_resolving(self):
        import asyncio
        import monitor_ofertas as monitor
        from unittest.mock import AsyncMock
        handlers = {}
        msg = messages()[0]
        msg.photo = SimpleNamespace(id=123)
        msg.document = None
        msg.grouped_id = None
        chat = SimpleNamespace(username='publicadorml')
        event = SimpleNamespace(message=msg, chat_id=CHAT, get_chat=AsyncMock(return_value=chat))
        client = Mock(); client.start = AsyncMock(); client.disconnect = AsyncMock()
        async def dialogs():
            yield SimpleNamespace(id=CHAT, entity=chat, is_group=True, is_channel=False)
        client.iter_dialogs = dialogs
        def on(event_type):
            def register(fn):
                handlers[type(event_type).__name__] = fn
                return fn
            return register
        client.on = on
        async def download(photo, file):
            Path(file).write_bytes(b'image')
            return file
        client.download_media = download
        async def events():
            await handlers['NewMessage'](event)
            await handlers['MessageEdited'](event)
            msg.raw_text = TEXT.replace('1.103,08', '999,00')
            await handlers['MessageEdited'](event)
        client.run_until_disconnected = events
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(monitor, 'BASE', Path(directory)), \
             patch.object(monitor, 'TelegramClient', return_value=client), \
             patch.object(monitor, 'resolve') as resolver, \
             patch('sys.argv', ['monitor_ofertas.py']), \
             patch.dict(os.environ, {'TG_API_ID': '123', 'TG_API_HASH': 'fake', 'TG_CHATS': '', 'TG_MEDIA_CHATS': '', 'TELEGRAM_CANAL': '@destino'}), \
             redirect_stdout(io.StringIO()):
            asyncio.run(monitor.main())
            rows = [json.loads(line) for line in (Path(directory) / 'fila_ofertas_v2.jsonl').read_text().splitlines()]
            self.assertEqual([r['price'] for r in rows], ['1.103,08', '999,00'])
            self.assertTrue(all(r['image'] for r in rows))
            self.assertTrue(all(r['url'] == URL for r in rows))
            resolver.assert_not_called()
