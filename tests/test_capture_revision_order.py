"""Regressões locais de frescor Telegram; nenhum cliente ou envio real."""
import asyncio
import json
import random
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fila_ofertas_sqlite import CapturedOfferQueue
from monitor_ofertas import CaptureCoordinator, source_revision_metadata
from tests.test_fila_ofertas_sqlite import offer
import monitor_ofertas as monitor


def revision(stamp, *, price='100,00', digest=None, recovered=False, chat=-123, message=7):
    row = offer('p1', chat=chat, message=message, price=price,
                digest=digest or str(stamp), recovered=recovered, stamp=1000)
    row['source_revision_at'] = datetime.fromtimestamp(stamp, timezone.utc).isoformat()
    row['source_revision_members'] = [(message, row['source_revision_at'])]
    return row


class CaptureRevisionOrderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'queue.sqlite3'
        self.queue = CapturedOfferQueue(self.path, now=lambda: 1100)
        self.addCleanup(lambda: self.queue.close())

    def test_recovery_current_enters_without_live(self):
        self.assertEqual(self.queue.replace_capture([revision(1001, recovered=True)]), 1)
        self.assertEqual(self.queue.pending()[0]['price'], '100,00')

    def test_live_old_cannot_replace_new_live_even_when_observed_later(self):
        self.queue.replace_capture([revision(1002, price='150,00')])
        old = revision(1001)
        old['captured_at'] = datetime.now(timezone.utc).isoformat()
        self.assertEqual(self.queue.replace_capture([old]), 0)
        self.assertEqual(self.queue.pending()[0]['price'], '150,00')

    def test_newer_recovery_can_replace_older_live(self):
        self.queue.replace_capture([revision(1001)])
        self.assertEqual(self.queue.replace_capture([revision(1002, price='150,00', recovered=True)]), 1)
        self.assertEqual(self.queue.pending()[0]['price'], '150,00')

    def test_same_revision_is_noop_without_changing_ttl_or_updated_at(self):
        row = revision(1001)
        self.queue.replace_capture([row])
        before = self.queue.db.execute('SELECT * FROM captured_queue').fetchall()
        self.assertEqual(self.queue.replace_capture([dict(row, recovered=True)], now=1200), 0)
        self.assertEqual(self.queue.db.execute('SELECT * FROM captured_queue').fetchall(), before)

    def test_price_edit_then_stale_recovery(self):
        self.queue.replace_capture([revision(1000)])
        self.queue.replace_capture([revision(1001, price='150,00')])
        self.assertEqual(self.queue.replace_capture([revision(1000, recovered=True)]), 0)
        self.assertEqual(self.queue.pending()[0]['price'], '150,00')

    def test_coupon_text_image_and_product_edits_are_protected_as_one_source(self):
        old = revision(1001)
        new = dict(revision(1002), product_id='p2', coupon='NOVO', name='Texto novo', image='nova.jpg')
        self.queue.replace_capture([old])
        self.queue.replace_capture([new])
        self.queue.replace_capture([dict(old, recovered=True)])
        self.assertEqual(self.queue.pending(), json.loads(json.dumps([new])))

    def test_equal_timestamp_live_wins_over_recovery_in_both_orders(self):
        old = revision(1001, recovered=True, digest='recovery')
        new = revision(1001, price='150,00', digest='live')
        self.queue.replace_capture([old])
        self.queue.replace_capture([new])
        self.assertEqual(self.queue.pending()[0]['price'], '150,00')
        self.assertEqual(self.queue.replace_capture([old]), 0)

    def test_equal_timestamp_conflicts_of_same_provenance_are_rejected(self):
        for recovered in (False, True):
            first = revision(1001, recovered=recovered, message=8 + int(recovered))
            conflicting = dict(first, capture_digest='conflict', price='150,00')
            self.queue.replace_capture([first])
            self.assertEqual(self.queue.replace_capture([conflicting]), 0)
        self.assertEqual([r['price'] for r in self.queue.pending()], ['100,00', '100,00'])

    def test_album_uses_native_member_dates_and_deterministic_ids(self):
        date = datetime.fromtimestamp(1000, timezone.utc)
        a = SimpleNamespace(id=7, date=date, edit_date=None)
        b = SimpleNamespace(id=8, date=date, edit_date=date + timedelta(seconds=2))
        original = source_revision_metadata([b, a])
        self.assertEqual(original, source_revision_metadata([a, b]))
        b.edit_date += timedelta(seconds=1)
        newer = source_revision_metadata([a, b])
        old = dict(revision(1002), **original)
        new = dict(revision(1003, price='150,00'), **newer)
        self.queue.replace_capture([old])
        self.queue.replace_capture([new])
        self.assertEqual(self.queue.replace_capture([dict(old, recovered=True)]), 0)
        partial = revision(1004, recovered=True)
        self.assertEqual(self.queue.replace_capture([partial]), 0)
        self.assertEqual(self.queue.pending()[0]['price'], '150,00')

    def test_album_member_edit_below_unchanged_max_timestamp_advances_revision(self):
        old = revision(1005)
        old['source_revision_members'] = [(7, revision(1000)['source_revision_at']), (8, old['source_revision_at'])]
        new = dict(old, capture_digest='new', price='150,00')
        new['source_revision_members'] = [(7, revision(1001)['source_revision_at']), (8, old['source_revision_at'])]
        self.queue.replace_capture([old])
        self.assertEqual(self.queue.replace_capture([new]), 1)
        self.assertEqual(self.queue.replace_capture([dict(old, recovered=True)]), 0)

    def test_restart_retains_revision_authority(self):
        self.queue.replace_capture([revision(1002, price='150,00')])
        self.queue.close()
        self.queue = CapturedOfferQueue(self.path, now=lambda: 1100)
        self.assertEqual(self.queue.replace_capture([revision(1001, recovered=True)]), 0)
        self.assertEqual(self.queue.pending()[0]['price'], '150,00')

    def test_different_messages_in_same_chat_are_independent(self):
        self.queue.replace_capture([revision(1005, message=7)])
        self.assertEqual(self.queue.replace_capture([revision(1001, message=8)]), 1)
        self.assertEqual({r['message_id'] for r in self.queue.pending()}, {7, 8})

    def test_identical_content_advances_authority_without_reenqueue(self):
        row = revision(1001, digest='same')
        self.queue.replace_capture([row])
        before = self.queue.db.execute('SELECT expires_at,updated_at FROM captured_queue').fetchone()
        newer = revision(1003, digest='same')
        metadata = {key: newer[key] for key in ('source_revision_at', 'source_revision_members')}
        self.assertTrue(self.queue.advance_revision(-123, 7, 'same', metadata))
        self.assertFalse(self.queue.advance_revision(-123, 7, 'wrong', metadata))
        self.assertEqual(self.queue.replace_capture([revision(1002, recovered=True)]), 0)
        self.assertEqual(self.queue.db.execute('SELECT expires_at,updated_at FROM captured_queue').fetchone(), before)

    def test_legacy_schema_migrates_additively_and_preserves_rows(self):
        legacy = Path(self.temp.name) / 'legacy.sqlite3'
        row = offer('p1')
        with sqlite3.connect(legacy) as db:
            db.execute('''CREATE TABLE captured_queue (
                queue_key TEXT PRIMARY KEY, product TEXT NOT NULL,chat_id TEXT,message_id INTEGER,
                payload TEXT NOT NULL,capture_digest TEXT,source_date TEXT,recovered INTEGER NOT NULL,
                enqueued_at REAL NOT NULL,updated_at REAL NOT NULL,expires_at REAL NOT NULL)''')
            db.execute('INSERT INTO captured_queue VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                       ('tg:-123:7:p1', 'p1', '-123', 7, json.dumps(row), 'd1', row['source_date'], 0, 1000, 1000, 8200))
        queue = CapturedOfferQueue(legacy, now=lambda: 1100)
        try:
            self.assertEqual(queue.pending(), [row])
            columns = {r[1] for r in queue.db.execute('PRAGMA table_info(captured_queue)')}
            self.assertTrue({'source_revision_at', 'source_revision_members'} <= columns)
            self.assertEqual(queue.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(queue.replace_capture([revision(999, recovered=True)]), 0)
            # Mesmo um timestamp maior não comprova frescor contra edit_date perdido.
            self.assertEqual(queue.replace_capture([revision(1005, recovered=True)]), 0)
            self.assertEqual(queue.replace_capture([revision(1005)]), 0)
            self.assertIsNone(queue.db.execute('SELECT source_revision_at FROM captured_queue').fetchone()[0])
        finally:
            queue.close()

    def test_threaded_recovery_live_stress_with_independent_sqlite_connections(self):
        # 8 writers, 2.400 callbacks, 12 origens: comparação deve ser atômica.
        jobs = [revision(1001 + n, chat=-1 - (n % 3), message=7 + (n % 4),
                         recovered=bool(n % 2), price=str(1001 + n)) for n in range(2400)]
        random.Random(42).shuffle(jobs)
        def worker(batch):
            queue = CapturedOfferQueue(self.path, now=lambda: 1100)
            try:
                for row in batch:
                    queue.replace_capture([row])
            finally:
                queue.close()
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(worker, [jobs[n::8] for n in range(8)]))
        expected = {}
        for row in jobs:
            key = (row['chat_id'], row['message_id'])
            expected[key] = max(expected.get(key, 0), int(row['price']))
        actual = {(r['chat_id'], r['message_id']): int(r['price']) for r in self.queue.pending()}
        self.assertEqual(actual, expected)
        self.assertEqual(self.queue.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')


class MonitorRecoveryRaceTests(unittest.TestCase):
    def test_real_monitor_recovery_snapshot_cannot_overwrite_live_edit(self):
        self._run_real_race()

    def test_real_monitor_album_recovery_cannot_overwrite_live_edit(self):
        self._run_real_race(album=True)

    def _run_real_race(self, album=False):
        handlers = {}
        date = datetime.now(timezone.utc)
        def message(price, edited=False):
            return SimpleNamespace(id=555, raw_text=f'Produto\nR$ {price}\nhttps://shopee.com.br/product/1/2',
                                   date=date, edit_date=date + timedelta(seconds=1) if edited else None,
                                   photo=None, document=None, grouped_id=None, noforwards=False,
                                   get_entities_text=lambda: [], reply_markup=None)
        old, new = message('100,00'), message('150,00', True)
        other = message('')
        other.id, other.raw_text = 556, ''
        if album:
            old.grouped_id = new.grouped_id = other.grouped_id = 321
        chat = SimpleNamespace(username='audit-local', noforwards=False)
        event = SimpleNamespace(message=new, chat_id=-123, get_chat=AsyncMock(return_value=chat),
                                get_input_chat=AsyncMock(return_value=chat))
        client = Mock()
        client.start = AsyncMock()
        client.disconnect = AsyncMock()
        client.run_until_disconnected = AsyncMock()
        client.get_messages = AsyncMock(return_value=[old, other])
        async def dialogs():
            yield SimpleNamespace(id=-123, entity=chat, is_group=False, is_channel=True)
        async def recent(entity, limit):
            yield old  # snapshot já obtido; edição chega durante a coleta.
            if album:
                yield other
            await handlers['MessageEdited'](event)
        def on(kind):
            def register(callback):
                handlers[type(kind).__name__] = callback
                return callback
            return register
        client.iter_dialogs, client.iter_messages, client.on = dialogs, recent, on
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(monitor, 'BASE', Path(directory)), \
             patch.object(monitor, 'TelegramClient', return_value=client), \
             patch.object(monitor, 'resolve', return_value=('Shopee:1:2', 'Shopee', 'https://shopee.com.br/product/1/2')), \
             patch('requests.sessions.Session.request', side_effect=AssertionError('Network forbidden')), \
             patch('sys.argv', ['monitor_ofertas.py']), \
             patch.dict('os.environ', {'TG_API_ID': '123', 'TG_API_HASH': 'fake', 'TG_CHATS': '-123',
                                      'TG_MEDIA_CHATS': '', 'TG_PAUSED_CHATS': '', 'TG_ESPELHO_CHATS': '',
                                      'TG_REBRAND_CHATS': '', 'TELEGRAM_CANAL': '@audit-destino',
                                      'TG_RECUPERAR_MINUTOS': '60', 'ML_MANUAL_CHAT': ''}), \
             patch('builtins.print'):
            asyncio.run(monitor.main())
            queue = CapturedOfferQueue(Path(directory) / 'publicacoes.sqlite3')
            try:
                rows = queue.pending()
                self.assertEqual([r['price'] for r in rows], ['150,00'])
                self.assertFalse(rows[0]['recovered'])
                self.assertEqual([key for key, _ in rows[0]['source_revision_members']],
                                 [555, 556] if album else [555])
                state = json.loads((Path(directory) / 'monitor_recuperacao.json').read_text())
                self.assertEqual(state['-123']['last_activity_at'], new.edit_date.isoformat())
            finally:
                queue.close()

    def test_exception_and_cancellation_release_chat_lock_and_semaphore(self):
        async def scenario():
            coordinator = CaptureCoordinator(1)
            async def fail():
                raise ValueError('local failure')
            with self.assertRaises(ValueError):
                await coordinator.run(-1, fail)
            entered = asyncio.Event()
            async def waiting():
                entered.set()
                await asyncio.Event().wait()
            task = asyncio.create_task(coordinator.run(-1, waiting))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(await asyncio.wait_for(coordinator.run(-1, lambda: asyncio.sleep(0, result='ok')), 1), 'ok')
            # Cancelar um waiter não libera o lock que pertence a outra task.
            entered.clear()
            owner = asyncio.create_task(coordinator.run(-1, waiting))
            await entered.wait()
            waiter = asyncio.create_task(coordinator.run(-1, waiting))
            await asyncio.sleep(0)
            waiter.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiter
            other = asyncio.create_task(coordinator.run(-2, lambda: asyncio.sleep(0, result='other')))
            await asyncio.sleep(0)
            self.assertFalse(other.done())
            owner.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await owner
            self.assertEqual(await asyncio.wait_for(other, 1), 'other')
        asyncio.run(scenario())


if __name__ == '__main__':
    unittest.main()
