"""Reconciliação administrativa local: seleção exata, rollback e concorrência."""
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import reconciliar_reservas as reconcile
from ofertas_core import Ledger
from inteligencia_ofertas import Intelligence
from fila_ofertas_sqlite import CapturedOfferQueue
from publisher_backoff import PublisherBackoff
from runtime_metrics import RuntimeMetrics
from multicanal_shadow import ShadowDistribution
from prepublicacao import PrePublicationGate


class AtomicReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'audit.sqlite3'
        self.ledger = Ledger(self.path)
        self.db = self.ledger.db
        self.addCleanup(self.db.close)
        self.channel_patch = patch.object(reconcile, 'TELEGRAM_CHANNEL', '@audit')
        self.channel_patch.start()
        self.addCleanup(self.channel_patch.stop)
        Intelligence(self.db)
        PublisherBackoff(self.db)
        RuntimeMetrics(self.db)
        ShadowDistribution(self.db)
        PrePublicationGate(self.temp.name, self.db)
        queue = CapturedOfferQueue(self.path, now=lambda: 1000)
        queue.replace_capture([{'product_id': 'OTHER', 'chat_id': -1, 'message_id': 9,
                                'source_date': '1970-01-01T00:16:40+00:00',
                                'source_revision_at': '1970-01-01T00:16:40+00:00'}])
        queue.close()
        self.db.execute("INSERT INTO radar_queue VALUES ('OTHER','{}',1000,9999999999)")
        self.db.execute("INSERT INTO publication_clock VALUES (2,9999999999)")
        self.db.commit()

    def add(self, product='P:1', day='2026-10-02', payload=None):
        self.db.execute('INSERT INTO posts(product,day,status) VALUES (?,?,?)', (product, day, 'uncertain'))
        self.ledger.deliveries.record_uncertain(product, destination='telegram', account='@audit',
                                                surface='channel', day=day, payload=payload)

    def snapshot(self):
        with sqlite3.connect(self.path) as reader:
            return list(reader.iterdump())

    def assert_error_unchanged(self, callback):
        before = self.snapshot()
        with self.assertRaises(SystemExit):
            callback()
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.db.in_transaction)

    def test_ambiguous_not_sent_rolls_back_and_keeps_publication_blocked(self):
        self.add(day='2026-10-01')
        self.add(day='2026-10-02')
        self.assert_error_unchanged(lambda: reconcile.resolve_not_sent(self.db, 'P:1', None))
        self.assertIsNone(self.ledger.reserve('P:1'))

    def test_ambiguous_sent_never_chooses_first_result(self):
        self.add(day='2026-10-01')
        self.add(day='2026-10-02')
        self.assert_error_unchanged(lambda: reconcile.resolve_sent(self.db, 'P:1', None, 77))

    def test_zero_matches_has_no_side_effects_for_either_command(self):
        self.add()
        for command in (lambda: reconcile.resolve_not_sent(self.db, 'missing', None),
                        lambda: reconcile.resolve_sent(self.db, 'missing', None, 77)):
            self.assert_error_unchanged(command)

    def test_sent_updates_both_and_records_known_price_history(self):
        payload = {'product_id': 'P:1', 'store': 'KaBuM', 'price': '150,00'}
        self.add(payload=payload)
        reconcile.resolve_sent(self.db, 'P:1', None, 77)
        self.assertEqual(self.db.execute('SELECT status,message_id FROM posts').fetchone(), ('sent', 77))
        self.assertEqual(self.db.execute('SELECT state,external_id,payload FROM deliveries').fetchone(),
                         ('SENT', '77', json.dumps(payload, ensure_ascii=False, separators=(',', ':'))))
        self.assertEqual(self.db.execute('SELECT product,cents,channel,message_id FROM price_history').fetchone(),
                         ('P:1', 15000, '@audit', 77))

    def test_not_sent_releases_exactly_selected_day(self):
        self.add(day='2026-10-01')
        self.add(day='2026-10-02')
        self.add(product='OTHER-PRODUCT')
        reconcile.resolve_not_sent(self.db, 'P:1', '2026-10-02')
        self.assertEqual(self.db.execute('SELECT product,day,status FROM posts ORDER BY product,day').fetchall(),
                         [('OTHER-PRODUCT', '2026-10-02', 'uncertain'), ('P:1', '2026-10-01', 'uncertain')])
        self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0], 2)
        self.assertIsNone(self.ledger.reserve('P:1'))

    def test_sent_same_message_is_exact_noop(self):
        self.add(payload={'product_id': 'P:1', 'store': 'KaBuM', 'price': '150,00'})
        reconcile.resolve_sent(self.db, 'P:1', None, 77)
        before = self.snapshot()
        reconcile.resolve_sent(self.db, 'P:1', None, '77')
        self.assertEqual(self.snapshot(), before)

    def test_sent_conflicting_message_is_rejected(self):
        self.add()
        reconcile.resolve_sent(self.db, 'P:1', None, 77)
        self.assert_error_unchanged(lambda: reconcile.resolve_sent(self.db, 'P:1', None, 78))

    def test_not_sent_cannot_release_confirmed_sent(self):
        self.add()
        reconcile.resolve_sent(self.db, 'P:1', None, 77)
        self.assert_error_unchanged(lambda: reconcile.resolve_not_sent(self.db, 'P:1', None))

    def test_not_sent_cannot_release_sent_delivery_even_if_posts_uncertain(self):
        self.add()
        self.ledger.deliveries.record_sent('P:1', destination='telegram', account='@audit',
                                           surface='channel', day='2026-10-02', external_id=77)
        self.assert_error_unchanged(lambda: reconcile.resolve_not_sent(self.db, 'P:1', None))

    def test_failure_between_posts_and_delivery_rolls_back_everything(self):
        for action in ('UPDATE', 'DELETE'):
            with self.subTest(action=action):
                if not self.db.execute('SELECT 1 FROM posts').fetchone():
                    self.add(payload={'product_id': 'P:1', 'store': 'KaBuM', 'price': '150,00'})
                self.db.execute(f"CREATE TRIGGER fault BEFORE {action} ON deliveries BEGIN SELECT RAISE(ABORT,'local injected failure'); END")
                self.db.commit()
                before = self.snapshot()
                with self.assertRaises(sqlite3.IntegrityError):
                    if action == 'UPDATE':
                        reconcile.resolve_sent(self.db, 'P:1', None, 77)
                    else:
                        reconcile.resolve_not_sent(self.db, 'P:1', None)
                self.assertEqual(self.snapshot(), before)
                self.db.execute('DROP TRIGGER fault')
                self.db.commit()

    def test_silently_ignored_delivery_update_also_rolls_back(self):
        self.add()
        self.db.execute("CREATE TRIGGER fault BEFORE UPDATE ON deliveries BEGIN SELECT RAISE(IGNORE); END")
        self.db.commit()
        self.assert_error_unchanged(lambda: reconcile.resolve_sent(self.db, 'P:1', None, 77))

    def test_other_day_and_destination_remain_identical(self):
        self.add(day='2026-10-01')
        self.add(day='2026-10-02')
        self.ledger.deliveries.record_uncertain('P:1', destination='instagram', account='fake',
                                                surface='feed', day='2026-10-02')
        other = self.db.execute("SELECT * FROM deliveries WHERE destination='instagram' OR day='2026-10-01'").fetchall()
        reconcile.resolve_sent(self.db, 'P:1', '2026-10-02', 77)
        self.assertEqual(self.db.execute("SELECT * FROM deliveries WHERE destination='instagram' OR day='2026-10-01'").fetchall(), other)

    def test_other_destination_is_not_removed_by_not_sent(self):
        self.add()
        self.ledger.deliveries.record_sent('P:1', destination='instagram', account='fake',
                                          surface='feed', day='2026-10-02', external_id='IG-fake')
        before = self.db.execute("SELECT * FROM deliveries WHERE destination='instagram'").fetchall()
        reconcile.resolve_not_sent(self.db, 'P:1', None)
        self.assertEqual(self.db.execute('SELECT * FROM deliveries').fetchall(), before)

    def test_other_telegram_account_is_fail_closed(self):
        self.add()
        self.ledger.deliveries.record_uncertain('P:1', destination='telegram', account='@other',
                                                surface='channel', day='2026-10-02')
        self.assert_error_unchanged(lambda: reconcile.resolve_not_sent(self.db, 'P:1', None))
        self.assert_error_unchanged(lambda: reconcile.resolve_sent(self.db, 'P:1', None, 77))

    def test_invalid_message_ids_are_rejected_without_writes(self):
        self.add()
        for value in (None, True, 0, -1, 'x', 77.5, str(2**64)):
            self.assert_error_unchanged(lambda value=value: reconcile.resolve_sent(self.db, 'P:1', None, value))

    def test_legacy_missing_delivery_can_be_resolved_without_invented_history(self):
        self.db.execute("INSERT INTO posts(product,day,status) VALUES ('P:1','2026-10-02','uncertain')")
        self.db.commit()
        reconcile.resolve_sent(self.db, 'P:1', None, 77)
        self.assertEqual(self.db.execute('SELECT state,external_id FROM deliveries').fetchone(), ('SENT', '77'))
        self.assertEqual(self.db.execute('SELECT count(*) FROM price_history').fetchone()[0], 0)

    def test_conflicting_known_history_is_rejected_and_preserved(self):
        self.add(payload={'product_id': 'P:1', 'store': 'KaBuM', 'price': '150,00'})
        self.db.execute("INSERT INTO price_history(product,cents,published,channel,message_id,payload) "
                        "VALUES ('P:1',10000,1000,'@audit',77,'{}')")
        self.db.commit()
        self.assert_error_unchanged(lambda: reconcile.resolve_sent(self.db, 'P:1', None, 77))

    def test_repeated_not_sent_is_explicit_error_without_side_effects(self):
        self.add()
        reconcile.resolve_not_sent(self.db, 'P:1', None)
        self.assert_error_unchanged(lambda: reconcile.resolve_not_sent(self.db, 'P:1', None))

    def test_two_connections_conflicting_resolutions_only_one_wins(self):
        self.add()
        barrier = threading.Barrier(2)
        def worker(message):
            with sqlite3.connect(self.path, timeout=30) as connection:
                barrier.wait()
                try:
                    reconcile.resolve_sent(connection, 'P:1', None, message)
                    return message
                except SystemExit:
                    return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, (77, 78)))
        winners = [value for value in results if value is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(self.db.execute('SELECT status,message_id FROM posts').fetchone(), ('sent', winners[0]))
        self.assertEqual(self.db.execute('SELECT state,external_id FROM deliveries').fetchone(), ('SENT', str(winners[0])))
        self.assertEqual(self.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')


if __name__ == '__main__':
    unittest.main()
