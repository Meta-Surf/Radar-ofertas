import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import requests
from ofertas_core import price_info, coupon_page_links, extract_links, caption
from cupons_shopee import coupon_entries
from monitor_ofertas import (CaptureRevisions, configured_chat_values,
                             dialog_is_configured, resolve_for_capture)

EXAMPLE = '''🔥 Kit Ventoinha Pichau Ventus NX, ARGB, 5x120mm, Branco, PCH-VTNX5-WH01

💵 R$ 113 em 12x sem juros

🏷️ Resgate todos os cupons desta página:
R$ 10 OFF
https://s.shopee.com.br/7AdtY0XAva

✨ Link do produto:
https://s.shopee.com.br/5q8VxYgRdz

Conheça nossos grupos no WhatsApp
www.cacaprecodorocha.com.br

(anuncio)'''
COUPON = 'https://s.shopee.com.br/7AdtY0XAva'
PRODUCT = 'https://s.shopee.com.br/5q8VxYgRdz'


def message(text=EXAMPLE, identity=123):
    return SimpleNamespace(id=identity, raw_text=text, photo=SimpleNamespace(id=99),
                           document=None, date=datetime.now(timezone.utc),
                           get_entities_text=lambda: [], reply_markup=None)


class GroupMonitoringTests(unittest.TestCase):
    def test_paused_chat_matches_numeric_id_and_username_case_insensitively(self):
        dialog = SimpleNamespace(id=-1001234567890,
                                 entity=SimpleNamespace(username='CanalTeste'))
        with patch.dict('os.environ', {
            'TG_PAUSED_CHATS': '-1001234567890,@OUTROCANAL'
        }):
            paused = configured_chat_values('TG_PAUSED_CHATS')
        self.assertTrue(dialog_is_configured(dialog, paused))
        self.assertTrue(dialog_is_configured(dialog, {'@canalteste'}))
        self.assertFalse(dialog_is_configured(dialog, {'-1009999999999'}))

    def test_actual_offer_price_condition_and_separate_coupon(self):
        msg = message()
        info = price_info(EXAMPLE)
        self.assertEqual(info, {'price': '113,00', 'price_condition': 'em 12x sem juros', 'price_from': False})
        entries = coupon_entries([msg])
        self.assertEqual(entries, [{'url': COUPON, 'conditions': 'R$ 10 OFF'}])
        excluded = coupon_page_links(EXAMPLE) | {e['url'] for e in entries}
        self.assertEqual([u for u in extract_links(msg) if u not in excluded], [PRODUCT])
        rendered = caption(dict(info, store='Shopee', source='telegram'))
        self.assertIn('<b>R$ 113,00</b>\n\nem 12x sem juros', rendered)
        self.assertNotIn('103,00', rendered)

    def test_installments_are_not_mistaken_for_total(self):
        for text in ['12x de R$ 113', '12x R$ 113', 'Parcela: R$ 113',
                     'R$ 113 cada parcela', 'Cupom R$ 10 em 12x',
                     'R$ 113 em 12x de R$ 9,42']:
            with self.subTest(text=text): self.assertIsNone(price_info(text))
        for condition in ['em 12x sem juros', 'em até 10x sem juros', 'em 6 vezes', 'em 3 parcelas']:
            with self.subTest(condition=condition):
                self.assertEqual(price_info('R$ 113 ' + condition)['price_condition'], condition)

    def test_coupon_scope_ends_at_product_heading_or_first_url(self):
        for body in [f'Resgate cupons\nR$ 10 OFF\nLink do produto:\n{PRODUCT}',
                     f'Resgate cupons\nR$ 10 OFF\nNotebook\n{PRODUCT}']:
            self.assertEqual(coupon_page_links(body), set())
        self.assertEqual(coupon_page_links(f'Resgate cupons\nR$ 10 OFF\n{COUPON}\n{PRODUCT}'), {COUPON})

    def test_repeated_metadata_edit_is_skipped_but_price_and_media_changes_are_not(self):
        cache = CaptureRevisions()
        msg = message()
        key, digest = cache.identity([msg], -123)
        cache.remember(key, digest)
        msg.edit_date = datetime.now(timezone.utc)
        msg.views = 999
        self.assertTrue(cache.unchanged(*cache.identity([msg], -123)))
        msg.raw_text = EXAMPLE.replace('R$ 113', 'R$ 109')
        self.assertFalse(cache.unchanged(*cache.identity([msg], -123)))
        msg.raw_text = EXAMPLE
        msg.photo.id = 100
        self.assertFalse(cache.unchanged(*cache.identity([msg], -123)))
        self.assertFalse(cache.unchanged(*cache.identity([message()], -456)))
        self.assertFalse(cache.unchanged(*cache.identity([message(identity=124)], -123)))

    def test_hidden_link_changes_are_not_skipped(self):
        cache = CaptureRevisions()
        msg = message('Ver oferta')
        msg.get_entities_text = lambda: [(SimpleNamespace(url=PRODUCT), 'Ver oferta')]
        cache.remember(*cache.identity([msg], -123))
        msg.get_entities_text = lambda: [(SimpleNamespace(url=COUPON), 'Ver oferta')]
        self.assertFalse(cache.unchanged(*cache.identity([msg], -123)))

    def test_revision_memory_is_bounded_and_expires(self):
        cache = CaptureRevisions(limit=1, ttl=60)
        with patch('monitor_ofertas.time.monotonic', return_value=0):
            cache.remember('a', 'one')
        with patch('monitor_ofertas.time.monotonic', return_value=61):
            self.assertFalse(cache.unchanged('a', 'one'))
        cache.remember('b', 'two')
        self.assertEqual(list(cache.seen), ['b'])

    def test_network_timeout_retries_then_recovers(self):
        product = ('Shopee:1:2', 'Shopee', 'https://shopee.com.br/product/1/2')
        with patch('monitor_ofertas.resolve', side_effect=[requests.Timeout(), product]) as resolve, \
             patch('monitor_ofertas.asyncio.sleep', new_callable=AsyncMock):
            self.assertEqual(asyncio.run(resolve_for_capture(PRODUCT)), (product, ''))
            self.assertEqual(resolve.call_count, 2)

    def test_http_403_is_reported_without_repeated_requests(self):
        def blocked(url, report): report('HTTP 403 em s.shopee.com.br')
        with patch('monitor_ofertas.resolve', side_effect=blocked) as resolve:
            product, reason = asyncio.run(resolve_for_capture(PRODUCT))
            self.assertIsNone(product)
            self.assertIn('HTTP 403', reason)
            self.assertEqual(resolve.call_count, 1)

    def test_http_429_retries_are_bounded(self):
        def limited(url, report): report('HTTP 429 em s.shopee.com.br')
        with patch('monitor_ofertas.resolve', side_effect=limited) as resolve, \
             patch('monitor_ofertas.asyncio.sleep', new_callable=AsyncMock):
            product, reason = asyncio.run(resolve_for_capture(PRODUCT))
            self.assertIsNone(product)
            self.assertIn('HTTP 429', reason)
            self.assertEqual(resolve.call_count, 3)

class CaptureFlowTests(unittest.TestCase):
    def test_paused_chat_is_not_registered_in_live_handlers(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import Mock
        import monitor_ofertas as monitor

        registered = []
        paused_entity = SimpleNamespace(username='pausado')
        active_entity = SimpleNamespace(username='ativo')
        client = Mock()
        client.start = AsyncMock()
        client.disconnect = AsyncMock()
        client.run_until_disconnected = AsyncMock()

        async def dialogs():
            yield SimpleNamespace(id=-123, name='Pausado', entity=paused_entity,
                                  is_group=False, is_channel=True)
            yield SimpleNamespace(id=-456, name='Ativo', entity=active_entity,
                                  is_group=False, is_channel=True)
        client.iter_dialogs = dialogs

        def on(event_type):
            registered.append(list(getattr(event_type, 'chats', []) or []))
            return lambda fn: fn
        client.on = on

        with tempfile.TemporaryDirectory() as directory, \
             patch.object(monitor, 'BASE', Path(directory)), \
             patch.object(monitor, 'TelegramClient', return_value=client), \
             patch('sys.argv', ['monitor_ofertas.py']), \
             patch.dict('os.environ', {
                 'TG_API_ID': '123', 'TG_API_HASH': 'fake',
                 'TG_CHATS': '-123,-456', 'TG_PAUSED_CHATS': '-123',
                 'TG_MEDIA_CHATS': '-123,-456', 'TG_ESPELHO_CHATS': '',
                 'TG_REBRAND_CHATS': '', 'TELEGRAM_CANAL': '@destino',
                 'TG_RECUPERAR_MINUTOS': '0', 'ML_MANUAL_CHAT': '',
             }), \
             patch('builtins.print'):
            asyncio.run(monitor.main())

        self.assertEqual(len(registered), 3)
        for chats in registered:
            self.assertIn(-456, chats)
            self.assertNotIn(-123, chats)

    def test_real_capture_skips_coupon_resolver_and_identical_edit(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import Mock
        import monitor_ofertas as monitor
        handlers = {}
        msg = message()
        msg.grouped_id = None
        chat = SimpleNamespace(username='origem')
        event = SimpleNamespace(message=msg, chat_id=-123, get_chat=AsyncMock(return_value=chat))
        client = Mock()
        client.start = AsyncMock()
        client.disconnect = AsyncMock()
        async def dialogs():
            yield SimpleNamespace(id=-123, entity=chat, is_group=False, is_channel=True)
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
            msg.raw_text = EXAMPLE.replace('R$ 113', 'R$ 109')
            await handlers['MessageEdited'](event)
        client.run_until_disconnected = events
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(monitor, 'BASE', Path(directory)), \
             patch.object(monitor, 'TelegramClient', return_value=client), \
             patch.object(monitor, 'resolve', return_value=('Shopee:1:2', 'Shopee', 'https://shopee.com.br/product/1/2')) as resolver, \
             patch('sys.argv', ['monitor_ofertas.py']), \
             patch.dict('os.environ', {'TG_API_ID':'123', 'TG_API_HASH':'fake', 'TG_CHATS':'-123', 'TG_MEDIA_CHATS':'-123', 'TELEGRAM_CANAL':'@destino', 'TG_RECUPERAR_MINUTOS':'0'}), \
             patch('builtins.print'):
            asyncio.run(monitor.main())
            rows = [json.loads(line) for line in (Path(directory) / 'fila_ofertas_v2.jsonl').read_text().splitlines()]
            offers = [row for row in rows if row['product_id'].startswith('Shopee:')]
            self.assertEqual([row['price'] for row in offers], ['113,00', '109,00'])
            self.assertTrue(all(row['image'] for row in offers))
            self.assertEqual([call.args[0] for call in resolver.call_args_list], [PRODUCT, PRODUCT])
            import sqlite3
            db = sqlite3.connect(Path(directory) / 'publicacoes.sqlite3')
            metric = db.execute(
                'SELECT status,captured,source_username FROM source_messages '
                'WHERE chat_id=? AND source_message_id=?', ('-123', 123)
            ).fetchone()
            db.close()
            self.assertEqual(metric, ('CAPTADA', 1, 'origem'))

    def test_startup_recovery_replays_recent_message_once_and_live_duplicate_is_skipped(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import Mock
        import monitor_ofertas as monitor

        handlers = {}
        msg = message(identity=555)
        msg.grouped_id = None
        msg.noforwards = False
        chat = SimpleNamespace(username='origem', noforwards=False)
        event = SimpleNamespace(message=msg, chat_id=-123, get_chat=AsyncMock(return_value=chat))

        client = Mock()
        client.start = AsyncMock()
        client.disconnect = AsyncMock()

        async def dialogs():
            yield SimpleNamespace(id=-123, entity=chat, is_group=False, is_channel=True)
        client.iter_dialogs = dialogs

        async def recent(entity, limit):
            self.assertIs(entity, chat)
            self.assertEqual(limit, 500)
            yield msg
        client.iter_messages = recent

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
            # Simula o mesmo update chegando ao vivo depois da varredura inicial.
            await handlers['NewMessage'](event)
        client.run_until_disconnected = events

        with tempfile.TemporaryDirectory() as directory, \
             patch.object(monitor, 'BASE', Path(directory)), \
             patch.object(monitor, 'TelegramClient', return_value=client), \
             patch.object(monitor, 'resolve', return_value=('Shopee:1:2', 'Shopee', 'https://shopee.com.br/product/1/2')) as resolver, \
             patch('sys.argv', ['monitor_ofertas.py']), \
             patch.dict('os.environ', {
                 'TG_API_ID': '123',
                 'TG_API_HASH': 'fake',
                 'TG_CHATS': '-123',
                 'TG_MEDIA_CHATS': '-123',
                 'TELEGRAM_CANAL': '@destino',
                 'TG_RECUPERAR_MINUTOS': '60',
                 'TG_RECUPERAR_MAX_MENSAGENS': '500',
                 'ML_MANUAL_CHAT': '',
             }), \
             patch('builtins.print'):
            asyncio.run(monitor.main())

            queue = Path(directory) / 'fila_ofertas_v2.jsonl'
            rows = [json.loads(line) for line in queue.read_text(encoding='utf-8').splitlines()]
            offers = [row for row in rows if row.get('product_id') == 'Shopee:1:2']
            self.assertEqual(len(offers), 1)
            self.assertTrue(offers[0].get('capture_digest'))
            self.assertTrue(offers[0].get('recovered'))
            self.assertEqual(resolver.call_count, 1)

            state = json.loads((Path(directory) / 'monitor_recuperacao.json').read_text(encoding='utf-8'))
            self.assertEqual(state['-123']['last_message_id'], 555)

    def test_queue_digest_reader_uses_latest_revision(self):
        import json
        import tempfile
        from pathlib import Path
        import monitor_ofertas as monitor

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fila_ofertas_v2.jsonl'
            rows = [
                {'chat_id': -123, 'message_id': 7, 'capture_digest': 'old'},
                {'chat_id': -123, 'message_id': 7, 'capture_digest': 'new'},
                {'chat_id': -456, 'message_id': 8},
            ]
            path.write_text('\n'.join(json.dumps(row) for row in rows) + '\n', encoding='utf-8')
            self.assertEqual(
                monitor.queued_revision_digests(path),
                {('-123', 7): 'new'},
            )