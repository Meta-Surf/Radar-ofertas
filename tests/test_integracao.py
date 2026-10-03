from tests.publisher_fixtures import persisted_rows
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ofertas_core import Ledger
from execucao_unica import instancia_unica
import bot_ofertas_revisao as publisher
import radar_shopee_continuo as radar


class IntegrationTests(unittest.TestCase):
    def test_radar_wait_does_not_delay_groups_or_reset_on_group_send(self):
        def offer(item, source):
            return dict(product_id=f'Shopee:1:{item}', url=f'https://shopee.com.br/product/1/{item}',
                        price='99,90', source=source, source_date=datetime.now(timezone.utc).isoformat(),
                        api_image='https://x.susercontent.com/a.jpg')
        g1, g2 = offer(1, 'telegram'), offer(2, 'telegram')
        r1, r2 = offer(3, 'shopee_api'), offer(4, 'shopee_api')
        client = Mock()
        client.prepare.side_effect = lambda o: dict(
            o, store='Shopee',
            affiliate_url='https://s.shopee.com.br/gateok',
            affiliate_generated=True, price_from=False,
        )
        with tempfile.TemporaryDirectory() as d:
            ledger = Ledger(Path(d) / 'publicacoes.sqlite3')
            try:
                from inteligencia_ofertas import Intelligence
                Intelligence(ledger.db).enqueue([r1,r2], now=1000)
                with patch.object(publisher, 'BASE', Path(d)), \
                     patch.object(publisher, 'Ledger', return_value=ledger), \
                     patch.object(publisher.ShopeeAffiliate, 'from_env', return_value=client), \
                     patch.object(publisher, 'rows', side_effect=[persisted_rows([r1, g1], d), persisted_rows([r1], d), persisted_rows([r2, g2], d), persisted_rows([r2], d)]), \
                     patch.object(publisher, 'send', return_value=(123, 0)) as send, \
                     patch.object(publisher.time, 'sleep', side_effect=[None, None, None, KeyboardInterrupt]), \
                     patch('ofertas_core.time.time', return_value=1000), \
                     patch.dict('os.environ', {'TELEGRAM_TOKEN':'fake', 'TELEGRAM_CANAL':'@fake'}), \
                     patch('builtins.print'):
                    with self.assertRaises(KeyboardInterrupt):
                        publisher.run_publisher(SimpleNamespace(simular=False), Mock())
                    self.assertEqual([call.args[2]['product_id'] for call in send.call_args_list],
                                     ['Shopee:1:1', 'Shopee:1:3', 'Shopee:1:2'])
                    self.assertEqual(ledger.publication_delay(clock_id=2), 1200)
            finally:
                ledger.db.close()

    def test_permanent_telegram_400_does_not_pause_next_offer(self):
        from telegram_api import TelegramSendError

        def offer(item):
            return dict(
                product_id=f'Shopee:1:{item}',
                url=f'https://shopee.com.br/product/1/{item}',
                price='99,90',
                source='telegram',
                source_date=datetime.now(timezone.utc).isoformat(),
                api_image='https://x.susercontent.com/a.jpg',
            )

        first, second = offer(11), offer(12)
        client = Mock()
        client.prepare.side_effect = lambda o: dict(
            o, store='Shopee',
            affiliate_url='https://s.shopee.com.br/gateok',
            affiliate_generated=True, price_from=False,
        )
        with tempfile.TemporaryDirectory() as d:
            ledger = Ledger(Path(d) / 'publicacoes.sqlite3')
            send_error = TelegramSendError(
                'permanent', "Bad Request: can't parse entities", error_code=400
            )
            with patch.object(publisher, 'BASE', Path(d)), \
                 patch.object(publisher, 'Ledger', return_value=ledger), \
                 patch.object(publisher.ShopeeAffiliate, 'from_env', return_value=client), \
                 patch.object(publisher, 'rows', return_value=persisted_rows([first, second], d)), \
                 patch.object(publisher, 'send', side_effect=[send_error, (321, 0)]) as send, \
                 patch.object(publisher.time, 'sleep', side_effect=KeyboardInterrupt), \
                 patch.dict('os.environ', {'TELEGRAM_TOKEN':'fake', 'TELEGRAM_CANAL':'@fake'}), \
                 patch('builtins.print'):
                with self.assertRaises(KeyboardInterrupt):
                    publisher.run_publisher(SimpleNamespace(simular=False), Mock())
            self.assertEqual(send.call_count, 2)
            self.assertEqual(
                ledger.db.execute("SELECT COUNT(*) FROM posts WHERE status='sent'").fetchone()[0],
                1,
            )
            self.assertEqual(ledger.publication_delay(clock_id=3), 0)
            ledger.db.close()

    def test_transient_telegram_5xx_does_not_pause_next_offer(self):
        from telegram_api import TelegramSendError

        def offer(item):
            return dict(
                product_id=f'Shopee:2:{item}',
                url=f'https://shopee.com.br/product/2/{item}',
                price='99,90', source='telegram',
                source_date=datetime.now(timezone.utc).isoformat(),
                api_image='https://x.susercontent.com/a.jpg',
            )

        first, second = offer(21), offer(22)
        client = Mock()
        client.prepare.side_effect = lambda o: dict(
            o, store='Shopee', affiliate_url='https://s.shopee.com.br/gateok',
            affiliate_generated=True, price_from=False,
        )
        with tempfile.TemporaryDirectory() as d:
            ledger = Ledger(Path(d) / 'publicacoes.sqlite3')
            error = TelegramSendError(
                'transient', 'Bad Gateway', error_code=502, retry_after=30
            )
            with patch.object(publisher, 'BASE', Path(d)), \
                 patch.object(publisher, 'Ledger', return_value=ledger), \
                 patch.object(publisher.ShopeeAffiliate, 'from_env', return_value=client), \
                 patch.object(publisher, 'rows', return_value=persisted_rows([first, second], d)), \
                 patch.object(publisher, 'send', side_effect=[error, (322, 0)]) as send, \
                 patch.object(publisher.time, 'sleep', side_effect=KeyboardInterrupt), \
                 patch.dict('os.environ', {'TELEGRAM_TOKEN':'fake', 'TELEGRAM_CANAL':'@fake'}), \
                 patch('builtins.print'):
                with self.assertRaises(KeyboardInterrupt):
                    publisher.run_publisher(SimpleNamespace(simular=False), Mock())
            self.assertEqual(send.call_count, 2)
            self.assertEqual(ledger.publication_delay(clock_id=3), 0)
            ledger.db.close()

    def test_transient_backoff_persists_and_does_not_block_other_offer(self):
        from telegram_api import TelegramSendError

        now = datetime.now(timezone.utc).isoformat()
        first = dict(
            product_id='Shopee:8:81',
            url='https://shopee.com.br/product/8/81',
            price='99,90', source='telegram', source_date=now,
            api_image='https://x.susercontent.com/a.jpg',
        )
        second = dict(
            product_id='Shopee:8:82',
            url='https://shopee.com.br/product/8/82',
            price='89,90', source='telegram', source_date=now,
            api_image='https://x.susercontent.com/b.jpg',
        )
        client = Mock()
        client.prepare.side_effect = lambda o: dict(
            o, store='Shopee', affiliate_url='https://s.shopee.com.br/gateok',
            affiliate_generated=True, price_from=False,
        )

        with tempfile.TemporaryDirectory() as d:
            ledger = Ledger(Path(d) / 'publicacoes.sqlite3')
            error = TelegramSendError(
                'transient', 'Bad Gateway', error_code=502, retry_after=30
            )
            with patch.object(publisher, 'BASE', Path(d)), \
                 patch.object(publisher, 'Ledger', return_value=ledger), \
                 patch.object(publisher.ShopeeAffiliate, 'from_env', return_value=client), \
                 patch.object(publisher, 'rows', return_value=persisted_rows([dict(first), dict(second)], d)), \
                 patch.object(publisher, 'send', side_effect=[error, (701, 0)]) as send, \
                 patch.object(publisher.time, 'sleep', side_effect=KeyboardInterrupt), \
                 patch.dict('os.environ', {'TELEGRAM_TOKEN':'fake', 'TELEGRAM_CANAL':'@fake'}), \
                 patch('builtins.print'):
                with self.assertRaises(KeyboardInterrupt):
                    publisher.run_publisher(SimpleNamespace(simular=False), Mock())

            self.assertEqual(send.call_count, 2)
            retry = ledger.db.execute(
                "SELECT failures,next_at FROM publisher_retry WHERE retry_key=?",
                ('captured_queue:product:' + first['product_id'],),
            ).fetchone()
            self.assertIsNotNone(retry)
            self.assertEqual(retry[0], 1)
            self.assertGreater(retry[1], 0)

            with patch.object(publisher, 'BASE', Path(d)), \
                 patch.object(publisher, 'Ledger', return_value=ledger), \
                 patch.object(publisher.ShopeeAffiliate, 'from_env', return_value=client), \
                 patch.object(publisher, 'rows', return_value=persisted_rows([dict(first)], d)), \
                 patch.object(publisher, 'send') as send_again, \
                 patch.object(publisher.time, 'sleep', side_effect=KeyboardInterrupt), \
                 patch.dict('os.environ', {'TELEGRAM_TOKEN':'fake', 'TELEGRAM_CANAL':'@fake'}), \
                 patch('builtins.print'):
                with self.assertRaises(KeyboardInterrupt):
                    publisher.run_publisher(SimpleNamespace(simular=False), Mock())
            send_again.assert_not_called()
            ledger.db.close()

    def test_ml_affiliate_403_opens_its_circuit_without_blocking_shopee(self):
        from mercadolivre_afiliados import MercadoLivreSessionError

        now = datetime.now(timezone.utc).isoformat()
        ml_offer = dict(
            kind='ml_offer', source='telegram', store='Mercado Livre',
            product_id='MercadoLivre:4360643061',
            url='https://produto.mercadolivre.com.br/MLB-4360643061-_JM',
            name='Produto ML', price='199,90', source_date=now,
        )
        shopee_offer = dict(
            product_id='Shopee:8:88',
            url='https://shopee.com.br/product/8/88',
            price='89,90', source='telegram', source_date=now,
        )
        ml_client = Mock()
        ml_client.cookie = 'ssid=session-value-123456789'
        ml_client.csrf = 'csrf-token-123456'
        ml_client.tag = 'tag'
        ml_client.cache = {}
        ml_client.prepare.side_effect = MercadoLivreSessionError(
            403, 'Sessão de afiliado Mercado Livre recusada.'
        )
        shopee = Mock()
        shopee.prepare.side_effect = lambda o: dict(
            o, store='Shopee', affiliate_url='https://s.shopee.com.br/gateok',
            affiliate_generated=True, price_from=False,
        )
        reader = Mock()
        reader.close = Mock()

        with tempfile.TemporaryDirectory() as d:
            ledger = Ledger(Path(d) / 'publicacoes.sqlite3')
            with patch.object(publisher, 'BASE', Path(d)), \
                 patch.object(publisher, 'Ledger', return_value=ledger), \
                 patch.object(publisher.ShopeeAffiliate, 'from_env', return_value=shopee), \
                 patch.object(publisher.MercadoLivreAffiliate, 'from_env', return_value=ml_client), \
                 patch.object(publisher, 'rows', return_value=persisted_rows([ml_offer, shopee_offer], d)), \
                 patch('mercadolivre_auto.AutoReader', return_value=reader), \
                 patch.object(publisher, 'send', return_value=(811, 0)) as send, \
                 patch.object(publisher.time, 'sleep', side_effect=KeyboardInterrupt), \
                 patch.dict('os.environ', {
                     'TELEGRAM_TOKEN':'fake', 'TELEGRAM_CANAL':'@fake',
                     'TELEGRAM_ADMIN_CHAT':'', 'EXIGIR_IMAGEM':'0',
                 }), \
                 patch('builtins.print'):
                with self.assertRaises(KeyboardInterrupt):
                    publisher.run_publisher(SimpleNamespace(simular=False), Mock())

            send.assert_called_once()
            self.assertEqual(send.call_args.args[2]['product_id'], 'Shopee:8:88')
            state = ledger.db.execute(
                """SELECT state,failures,last_status,blocked_until
                   FROM ml_affiliate_session_health WHERE id=1"""
            ).fetchone()
            self.assertEqual(state[:3], ('invalid', 1, 403))
            self.assertGreater(state[3], 0)
            ledger.db.close()

    def test_telegram_429_pauses_global_queue(self):
        from telegram_api import TelegramSendError

        offer = dict(
            product_id='Shopee:3:31',
            url='https://shopee.com.br/product/3/31',
            price='99,90', source='telegram',
            source_date=datetime.now(timezone.utc).isoformat(),
            api_image='https://x.susercontent.com/a.jpg',
        )
        client = Mock()
        client.prepare.return_value = dict(
            offer, store='Shopee', affiliate_url='https://s.shopee.com.br/gateok',
            affiliate_generated=True, price_from=False,
        )
        with tempfile.TemporaryDirectory() as d:
            ledger = Ledger(Path(d) / 'publicacoes.sqlite3')
            error = TelegramSendError(
                'rate_limit', 'Too Many Requests', error_code=429, retry_after=90
            )
            with patch.object(publisher, 'BASE', Path(d)), \
                 patch.object(publisher, 'Ledger', return_value=ledger), \
                 patch.object(publisher.ShopeeAffiliate, 'from_env', return_value=client), \
                 patch.object(publisher, 'rows', return_value=persisted_rows([offer], d)), \
                 patch.object(publisher, 'send', side_effect=error) as send, \
                 patch.object(publisher.time, 'sleep', side_effect=KeyboardInterrupt), \
                 patch.dict('os.environ', {'TELEGRAM_TOKEN':'fake', 'TELEGRAM_CANAL':'@fake'}), \
                 patch('builtins.print'):
                with self.assertRaises(KeyboardInterrupt):
                    publisher.run_publisher(SimpleNamespace(simular=False), Mock())
            send.assert_called_once()
            self.assertGreater(ledger.publication_delay(clock_id=3), 0)
            ledger.db.close()

    def test_live_then_recovered_then_radar_order(self):
        offers = [
            dict(product_id='g1', source_date='2026-01-01'),
            dict(product_id='rec1', source_date='2026-01-03', recovered=True),
            dict(product_id='g2', source_date='2026-01-02'),
            dict(product_id='rec2', source_date='2026-01-04', recovered=True),
            dict(product_id='r1', source='shopee_api'),
        ]
        with patch.object(publisher, 'rows', return_value=iter(offers)):
            self.assertEqual(
                [o['product_id'] for o in publisher.ordered_rows()],
                ['g2', 'g1', 'rec2', 'rec1', 'r1'],
            )

    def test_ordered_rows_deduplicates_same_product_across_messages(self):
        offers = [
            dict(product_id='Shopee:1:1', source_date='2026-01-02',
                 chat_id=1, message_id=2, price='90,00'),
            dict(product_id='Shopee:1:1', source_date='2026-01-01',
                 chat_id=2, message_id=3, price='100,00'),
            dict(product_id='Shopee:1:2', source_date='2026-01-01',
                 chat_id=1, message_id=4, price='80,00'),
        ]
        with patch.object(publisher, 'rows', return_value=iter(offers)):
            rows = list(publisher.ordered_rows())
        self.assertEqual([o['product_id'] for o in rows],
                         ['Shopee:1:1', 'Shopee:1:2'])
        self.assertEqual(rows[0]['price'], '90,00')

    def test_ordered_rows_deduplicates_same_coupon_identity(self):
        offers = [
            dict(product_id='ShopeeCoupon:same', kind='coupon_alert',
                 source_date='2026-01-03', chat_id=1, message_id=30),
            dict(product_id='ShopeeCoupon:same', kind='coupon_alert',
                 source_date='2026-01-02', chat_id=1, message_id=20),
            dict(product_id='ShopeeCoupon:other', kind='coupon_alert',
                 source_date='2026-01-01', chat_id=1, message_id=10),
        ]
        with patch.object(publisher, 'rows', return_value=iter(offers)):
            rows = list(publisher.ordered_rows())
        self.assertEqual(
            [o['product_id'] for o in rows],
            ['ShopeeCoupon:same', 'ShopeeCoupon:other'],
        )
        self.assertEqual(rows[0]['message_id'], 30)

    def test_recovery_cooldown_does_not_block_eligible_radar(self):
        now = datetime.now(timezone.utc).isoformat()
        recovered = dict(
            product_id='Shopee:1:10',
            url='https://shopee.com.br/product/1/10',
            price='99,90',
            source='telegram',
            source_date=now,
            recovered=True,
            api_image='https://x.susercontent.com/recovered.jpg',
        )
        radar_offer = dict(
            product_id='Shopee:1:20',
            url='https://shopee.com.br/product/1/20',
            price='89,90',
            source='shopee_api',
            source_date=now,
            api_image='https://x.susercontent.com/radar.jpg',
            rating=5,
            sales=100,
            discount=30,
        )
        client = Mock()
        client.prepare.side_effect = lambda offer: dict(
            offer, store='Shopee',
            affiliate_url='https://s.shopee.com.br/gateok',
            affiliate_generated=True, price_from=False,
        )
        with tempfile.TemporaryDirectory() as d:
            ledger = Ledger(Path(d) / 'publicacoes.sqlite3')
            try:
                from inteligencia_ofertas import Intelligence
                intelligence = Intelligence(ledger.db)
                intelligence.enqueue([radar_offer])
                ledger.mark_attempt(30, clock_id=4)
                with patch.object(publisher, 'BASE', Path(d)), \
                     patch.object(publisher, 'Ledger', return_value=ledger), \
                     patch.object(publisher.ShopeeAffiliate, 'from_env', return_value=client), \
                     patch.object(publisher, 'rows', return_value=persisted_rows([recovered], d)), \
                     patch.object(publisher, 'send', return_value=(123, 0)) as send, \
                     patch.object(publisher.time, 'sleep', side_effect=[KeyboardInterrupt]), \
                     patch.dict('os.environ', {
                         'TELEGRAM_TOKEN': 'fake',
                         'TELEGRAM_CANAL': '@fake',
                         'INTERVALO_RECUPERADAS': '30',
                     }), \
                     patch('builtins.print'):
                    with self.assertRaises(KeyboardInterrupt):
                        publisher.run_publisher(SimpleNamespace(simular=False), Mock())
                    self.assertEqual(send.call_count, 1)
                    self.assertEqual(send.call_args.args[2]['product_id'], 'Shopee:1:20')
            finally:
                ledger.db.close()

    def test_cooldown_survives_restart(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / 'publicacoes.sqlite3'
            ledger = Ledger(db)
            with patch('ofertas_core.time.time', return_value=1000):
                ledger.mark_attempt(300)
            ledger.db.close()
            ledger = Ledger(db)
            with patch('ofertas_core.time.time', return_value=1100):
                self.assertEqual(ledger.publication_delay(), 200)
            ledger.db.close()

    def test_two_sources_share_product_reservation(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Ledger(Path(d) / 'publicacoes.sqlite3'), Ledger(Path(d) / 'publicacoes.sqlite3')
            day = a.reserve('Shopee:123:456')
            a.finish('Shopee:123:456', day, 123)
            self.assertIsNone(b.reserve('Shopee:123:456'))
            a.db.close()
            b.db.close()

    def test_publisher_lock_excludes_second_process(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'publisher.lock'
            with instancia_unica(path):
                with self.assertRaises(SystemExit):
                    with instancia_unica(path):
                        pass
            with instancia_unica(path):
                pass

    def test_radar_queue_never_generates_or_sends_link(self):
        client = Mock()
        args = SimpleNamespace(publicar=False, enfileirar=True, loop=True, limite=3, round_index=0, grupo=None)
        offer = dict(source='shopee_api', price='99,90', product_id='Shopee:1:2', name='Smart TV 55 polegadas', rating=5, sales=100, discount=30)
        spec = dict(theme='Televisores', group='Tecnologia', queries=['smart tv'],
                    min_price=0, min_discount=20, min_rating=4.5, min_sales=50, premium_terms=[])
        ledger = Ledger(':memory:')
        with patch('shopee_afiliados.ShopeeAffiliate.from_env', return_value=client), \
             patch('radar_shopee.collect', return_value=([offer], 1)), \
             patch('inteligencia_ofertas.Intelligence.enqueue') as enqueue, \
             patch('ofertas_core.Ledger', return_value=ledger), \
             patch.object(radar, 'load_catalog', return_value=(['Tecnologia'], [spec])), \
             patch.object(radar.time, 'sleep'), patch('builtins.print'), \
             patch.object(publisher, 'send') as send:
            radar.run_round(args, Mock())
            self.assertEqual(enqueue.call_args.args[0][0]['product_id'], 'Shopee:1:2')
            client.prepare.assert_not_called()
            send.assert_not_called()


if __name__ == '__main__':
    unittest.main()

