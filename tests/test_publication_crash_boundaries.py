"""Crashes reais em subprocessos e rede simulada; nenhum envio externo."""
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
from ofertas_core import Ledger
from distribuicao import DeliveryOutbox
from fila_ofertas_sqlite import CapturedOfferQueue
from telegram_api import send_telegram, TelegramSendError

ROOT = Path(__file__).resolve().parents[1]
CHILD = '''
import os, sys
from pathlib import Path
from datetime import datetime, timezone
from ofertas_core import Ledger
phase, filename = sys.argv[1:]
ledger = Ledger(filename)
offer = {'product_id':'P:1','store':'KaBuM','price':'100,00'}
day = ledger.reserve('P:1', moment=datetime(2026,10,2,15,tzinfo=timezone.utc), offer=offer, channel='@audit')
if phase == 'reserved': os._exit(77)
assert ledger.mark_sending('P:1', day)
if phase == 'sending': os._exit(77)
# Aceitação remota simulada, persistida independentemente do ledger.
Path(filename + '.accepted').write_text('77')
if phase == 'accepted': os._exit(77)
if phase == 'during_finish':
    ledger.db.create_function('crash_now', 0, lambda: os._exit(77))
    ledger.db.execute('CREATE TRIGGER crash BEFORE INSERT ON deliveries BEGIN SELECT crash_now(); END')
    ledger.db.commit()
ledger.finish('P:1', day, 77, offer=offer, channel='@audit')
os._exit(77)
'''


def response(body, status=200):
    return SimpleNamespace(status_code=status, json=lambda: body)


class PublicationCrashBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'local.sqlite3'
        self.ledger = Ledger(self.path)
        self.addCleanup(lambda: self.ledger.db.close())
        self.queue = CapturedOfferQueue(self.path)
        self.addCleanup(self.queue.close)
        self.offer = {'product_id':'P:1','store':'KaBuM','price':'100,00','chat_id':-1,'message_id':7,
                      'source_date':datetime.now(timezone.utc).isoformat()}
        self.offer['source_revision_at'] = self.offer['source_date']
        self.queue.replace_capture([self.offer])

    def crash(self, phase):
        result = subprocess.run([sys.executable, '-c', CHILD, phase, str(self.path)],
                                cwd=ROOT, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 77, result.stderr)
        self.ledger.db.close()
        self.ledger = Ledger(self.path)

    def begin_send(self):
        day = self.ledger.reserve('P:1', offer=self.offer, channel='@audit')
        self.assertTrue(self.ledger.mark_sending('P:1', day))
        return day

    def test_A_reserved_crash_before_sending_can_be_safely_released(self):
        self.crash('reserved')
        result = self.ledger.reconcile_reservations()
        self.assertEqual(result['released_abandoned_reserved'], 1)
        self.assertFalse(Path(str(self.path)+'.accepted').exists())
        self.assertIsNotNone(self.ledger.reserve('P:1'))

    def test_B_sending_crash_before_network_is_conservatively_uncertain(self):
        self.crash('sending')
        self.assertEqual(self.ledger.reconcile_reservations()['moved_to_uncertain'], 1)
        self.assertFalse(Path(str(self.path)+'.accepted').exists())
        self.assertIsNone(self.ledger.reserve('P:1'))

    def test_C_remote_accepted_then_crash_before_finish_never_resends(self):
        self.crash('accepted')
        self.assertTrue(Path(str(self.path)+'.accepted').exists())
        self.ledger.reconcile_reservations()
        self.assertIsNone(self.ledger.reserve('P:1'))
        self.assertEqual(self.ledger.db.execute('SELECT status FROM posts').fetchone()[0], 'uncertain')

    def test_D_timeout_and_connection_error_are_uncertain_without_retry(self):
        for error in (requests.Timeout, requests.ConnectionError):
            with self.subTest(error=error.__name__):
                product = 'P:' + error.__name__
                day = self.ledger.reserve(product)
                self.ledger.mark_sending(product, day)
                client = Mock()
                client.post.side_effect = error('local injected failure')
                with self.assertRaises(TelegramSendError) as caught:
                    send_telegram(client, 'fake', {'chat_id':'@audit','text':'local'})
                self.assertEqual(caught.exception.kind, 'uncertain')
                self.ledger.mark_uncertain(product, day, channel='@audit')
                self.assertIsNone(self.ledger.reserve(product))
                self.assertEqual(client.post.call_count, 1)
        self.assertEqual(self.ledger.deliveries.ready('telegram'), [])

    def test_E_explicit_rejection_can_release_reservation(self):
        day = self.begin_send()
        client = Mock()
        client.post.return_value = response({'ok':False,'error_code':400,'description':'invalid text'}, 400)
        with self.assertRaises(TelegramSendError) as caught:
            send_telegram(client, 'fake', {'chat_id':'@audit','text':'local'})
        self.assertEqual(caught.exception.kind, 'permanent')
        self.ledger.release('P:1', day)
        self.assertIsNotNone(self.ledger.reserve('P:1'))

    def test_F_429_preserves_queue_and_retry_after_clock(self):
        day = self.begin_send()
        client = Mock()
        client.post.return_value = response({'ok':False,'error_code':429,'parameters':{'retry_after':19}}, 429)
        with self.assertRaises(TelegramSendError) as caught:
            send_telegram(client, 'fake', {'chat_id':'@audit','text':'local'})
        self.assertEqual(caught.exception.kind, 'rate_limit')
        self.ledger.release('P:1', day)
        with patch('ofertas_core.time.time', return_value=1000):
            self.ledger.mark_attempt(caught.exception.retry_after, clock_id=3)
            self.assertEqual(self.ledger.publication_delay(clock_id=3), 19)
        self.assertEqual(len(self.queue.pending()), 1)
        self.assertIsNotNone(self.ledger.reserve('P:1'))

    def test_G_media_rejection_falls_back_with_exactly_one_success(self):
        day = self.begin_send()
        client = Mock()
        client.post.side_effect = [response({'ok':False,'error_code':400,'description':'invalid photo'},400),
                                   response({'ok':True,'result':{'message_id':77}})]
        message = send_telegram(client, 'fake', {'chat_id':'@audit','caption':'local'},
                                image='https://fixture.invalid/image.jpg')
        self.assertEqual(message, 77)
        self.assertEqual(client.post.call_count, 2)
        self.ledger.finish('P:1', day, message, offer=self.offer, channel='@audit')
        self.assertIsNone(self.ledger.reserve('P:1', offer=self.offer, channel='@audit'))

    def test_H_finish_committed_before_queue_discard_cannot_repeat_same_offer(self):
        self.crash('finished')
        self.ledger.reconcile_reservations()
        self.assertEqual(len(self.queue.pending()), 1)
        self.assertIsNone(self.ledger.reserve('P:1', offer=self.offer, channel='@audit'))
        self.assertEqual(self.ledger.db.execute('SELECT status,message_id FROM posts').fetchone(), ('sent',77))

    def test_I_crash_inside_finish_between_posts_and_outbox_rolls_back_both(self):
        self.crash('during_finish')
        self.assertEqual(self.ledger.db.execute('SELECT status FROM posts').fetchone()[0], 'sending')
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM deliveries').fetchone()[0], 0)
        self.ledger.reconcile_reservations()
        self.assertIsNone(self.ledger.reserve('P:1'))
        self.assertEqual(self.ledger.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_J_eight_connections_reserve_and_claim_only_once_per_key(self):
        outbox = self.ledger.deliveries
        for n in range(17):
            outbox.enqueue(f'CONCURRENT:{n}', destination='local', day='2026-10-02', payload={})
        ids = [row[0] for row in outbox.ready('local')]
        barrier = threading.Barrier(8)
        def worker(_):
            ledger = Ledger(self.path)
            try:
                barrier.wait()
                reservations = claims = 0
                for n in range(128):
                    key = n % 17
                    reservations += ledger.reserve(f'CONCURRENT:{key}') is not None
                    claims += ledger.deliveries.claim(ids[key])
                return reservations, claims
            finally:
                ledger.db.close()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(worker, range(8)))
        self.assertEqual(sum(n for n,_ in results), 17)
        self.assertEqual(sum(n for _,n in results), 17)
        self.assertEqual(self.ledger.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_current_startup_blocks_resend_even_before_next_uncertain_backfill(self):
        self.crash('accepted')
        self.assertEqual(self.ledger.deliveries.backfill_telegram_posts('@audit'), 0)
        self.ledger.reconcile_reservations()
        self.assertIsNone(self.ledger.reserve('P:1'))
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM deliveries').fetchone()[0], 0)
        self.assertEqual(self.ledger.deliveries.backfill_telegram_posts('@audit'), 1)
        self.assertEqual(self.ledger.deliveries.ready('telegram'), [])


if __name__ == '__main__':
    unittest.main()
