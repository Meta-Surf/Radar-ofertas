import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ofertas_core import Ledger
from execucao_unica import instancia_unica
import bot_ofertas_revisao as publisher
import radar_shopee_continuo as radar


class IntegrationTests(unittest.TestCase):
    def test_both_sources_are_interleaved(self):
        offers = [dict(product_id='g1', source_date='2026-01-01'),
                  dict(product_id='g2', source_date='2026-01-02'),
                  dict(product_id='r1', source='shopee_api')]
        with patch.object(publisher, 'rows', return_value=iter(offers)):
            self.assertEqual([o['product_id'] for o in publisher.ordered_rows('telegram')], ['g2', 'r1', 'g1'])
        with patch.object(publisher, 'rows', return_value=iter(offers)):
            self.assertEqual([o['product_id'] for o in publisher.ordered_rows('radar')], ['r1', 'g2', 'g1'])

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
        offer = dict(product_id='Shopee:1:2', name='Smart TV 55 polegadas', rating=5, sales=100, discount=30)
        ledger = Mock()
        ledger.db.execute.return_value = []
        with patch('shopee_afiliados.ShopeeAffiliate.from_env', return_value=client), \
             patch('radar_shopee.collect', return_value=([offer], 1)), \
             patch('radar_shopee.save_snapshot') as save, \
             patch('ofertas_core.Ledger', return_value=ledger), \
             patch.object(radar, 'TEMAS', [('Televisores', 'smart tv')]), \
             patch.object(radar.time, 'sleep'), patch('builtins.print'), \
             patch.object(publisher, 'send') as send:
            radar.run_round(args, Mock())
            self.assertEqual(save.call_args.args[0][0]['product_id'], 'Shopee:1:2')
            client.prepare.assert_not_called()
            send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
