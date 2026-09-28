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
        client.prepare.side_effect = lambda o: dict(o)
        with tempfile.TemporaryDirectory() as d:
            ledger = Ledger(Path(d) / 'posts.db')
            try:
                from inteligencia_ofertas import Intelligence
                Intelligence(ledger.db).enqueue([r1,r2], now=1000)
                with patch.object(publisher, 'Ledger', return_value=ledger), \
                     patch.object(publisher.ShopeeAffiliate, 'from_env', return_value=client), \
                     patch.object(publisher, 'rows', side_effect=[iter([r1, g1]), iter([r1]), iter([r2, g2]), iter([r2])]), \
                     patch.object(publisher, 'send', return_value=(123, 0)) as send, \
                     patch.object(publisher.time, 'sleep', side_effect=[None, None, None, KeyboardInterrupt]), \
                     patch('ofertas_core.time.time', return_value=1000), \
                     patch.dict('os.environ', {'TELEGRAM_TOKEN':'fake', 'TELEGRAM_CANAL':'@fake'}), \
                     patch('builtins.print'):
                    with self.assertRaises(KeyboardInterrupt):
                        publisher.run_publisher(SimpleNamespace(simular=False), Mock())
                    self.assertEqual([call.args[2]['product_id'] for call in send.call_args_list],
                                     ['Shopee:1:1', 'Shopee:1:3', 'Shopee:1:2'])
                    self.assertEqual(ledger.publication_delay(clock_id=2), 600)
            finally:
                ledger.db.close()

    def test_groups_have_priority_over_all_radar_offers(self):
        offers = [dict(product_id='g1', source_date='2026-01-01'),
                  dict(product_id='g2', source_date='2026-01-02'),
                  dict(product_id='r1', source='shopee_api')]
        with patch.object(publisher, 'rows', return_value=iter(offers)):
            self.assertEqual([o['product_id'] for o in publisher.ordered_rows()], ['g2', 'g1', 'r1'])

    def test_cooldown_survives_restart(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / 'posts.db'
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
            a, b = Ledger(Path(d) / 'posts.db'), Ledger(Path(d) / 'posts.db')
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
        args = SimpleNamespace(publicar=False, enfileirar=True, loop=True, limite=3, round_index=0)
        offer = dict(source='shopee_api', price='99,90', product_id='Shopee:1:2', name='Smart TV 55 polegadas', rating=5, sales=100, discount=30)
        ledger = Ledger(':memory:')
        with patch('shopee_afiliados.ShopeeAffiliate.from_env', return_value=client), \
             patch('radar_shopee.collect', return_value=([offer], 1)), \
             patch('inteligencia_ofertas.Intelligence.enqueue') as enqueue, \
             patch('ofertas_core.Ledger', return_value=ledger), \
             patch.object(radar, 'TEMAS', [('Televisores', 'smart tv')]), \
             patch.object(radar.time, 'sleep'), patch('builtins.print'), \
             patch.object(publisher, 'send') as send:
            radar.run_round(args, Mock())
            self.assertEqual(enqueue.call_args.args[0][0]['product_id'], 'Shopee:1:2')
            client.prepare.assert_not_called()
            send.assert_not_called()


if __name__ == '__main__':
    unittest.main()

