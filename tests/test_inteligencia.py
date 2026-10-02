import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, Mock
from inteligencia_ofertas import Intelligence, comparison_key, brand_match, DAY, TTL
from ofertas_core import Ledger, caption, price_info
from radar_shopee_continuo import publish_one


def offer(item=1, **kwargs):
    o = dict(product_id=f'Shopee:1:{item}', url=f'https://shopee.com.br/product/1/{item}',
             store='Shopee', name='Caixa de som JBL Bluetooth', tema_radar='Caixas de som',
             source='shopee_api', source_date=datetime.now(timezone.utc).isoformat(),
             price='100,00', rating=4.8, sales=100, discount=30,
             variant_id='modelo-preto', variant_verified=True, api_image='https://x.susercontent.com/a.jpg')
    o.update(kwargs)
    return o


class IntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger(':memory:')
        self.i = Intelligence(self.ledger.db)

    def tearDown(self):
        self.ledger.db.close()

    def record(self, o, when, message=1, channel='@canal'):
        with self.ledger.db:
            self.i.record(o, channel, message, when)

    def test_brand_scoped_and_no_compatibility_bonus(self):
        self.assertTrue(brand_match(offer()))
        self.assertFalse(brand_match(offer(name='Capa para caixa de som JBL')))
        self.assertFalse(brand_match(offer(name='Caixa de som genérica similar JBL')))
        self.assertTrue(brand_match(offer(name='Placa de video ASUS GeForce RTX 4060', tema_radar='Placas de vídeo')))
        self.assertFalse(brand_match(offer(name='Placa mae compativel AMD', tema_radar='Placas-mãe')))

    def test_queue_survives_restart_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'db'
            ledger = Ledger(path); i = Intelligence(ledger.db)
            i.enqueue([offer(), offer(price='90,00')], now=100)
            ledger.db.close()
            ledger = Ledger(path); i = Intelligence(ledger.db)
            self.assertEqual([o['price'] for o in i.pending(now=101)], ['90,00'])
            self.assertEqual(i.pending(now=100+TTL), [])
            ledger.db.close()

    def test_enqueue_does_not_clear_unseen_candidates(self):
        self.i.enqueue([offer(1)], now=100)
        self.i.enqueue([offer(2)], now=200)
        self.assertEqual(len(self.i.pending(now=201)), 2)
        self.i.enqueue([], now=300)
        self.assertEqual(len(self.i.pending(now=301)), 2)

    def test_preference_and_rotation(self):
        self.i.enqueue([offer(1,name='Caixa de som Generica'),offer(2)], now=100)
        self.assertEqual(self.i.pending(now=101)[0]['product_id'], 'Shopee:1:2')
        with self.ledger.db:
            self.ledger.db.execute('INSERT INTO radar_rotation VALUES (?,?)', ('Caixas de som',100))
        self.i.enqueue([offer(3,name='Tablet Samsung',tema_radar='Tablets')],now=100)
        self.assertEqual(self.i.pending(now=101)[0]['product_id'], 'Shopee:1:3')

    def test_history_windows_largest_and_ties(self):
        now=200*DAY
        self.record(offer(), now-181*DAY)
        self.record(offer(), now-DAY, 2)
        self.assertIn('180 dias',self.i.badge(offer(price='90,00'),'@canal',now))
        self.assertTrue(self.i.badge(offer(),'@canal',now).startswith('📉 Iguala'))
        self.assertEqual(self.i.badge(offer(price='110,00'),'@canal',now),'')
        self.assertEqual(self.i.badge(offer(price='90,00'),'@outro',now),'')

    def test_all_windows_and_insufficient_history(self):
        for days in range(15,181,15):
            self.ledger.db.execute('DELETE FROM price_history')
            self.record(offer(), 1)
            self.record(offer(), days*DAY-1,2)
            self.assertIn(f'{days} dias', self.i.badge(offer(price='90,00'),'@canal',days*DAY+1))
        self.assertEqual(self.i.badge(offer(price='90,00'),'@canal',14*DAY),'')

    def test_ambiguous_variant_and_conditions_do_not_compare(self):
        for changes in ({'variant_verified':False},{'variant_id':None},{'price_from':True}):
            self.assertIsNone(comparison_key(offer(**changes)))
        self.assertNotEqual(comparison_key(offer()), comparison_key(offer(variant_id='outro')))
        self.assertNotEqual(comparison_key(offer()), comparison_key(offer(coupon='CUPOM')))
        self.assertNotEqual(comparison_key(offer()), comparison_key(offer(price_condition='no pix')))
        self.assertNotEqual(comparison_key(offer()), comparison_key(offer(source='telegram')))

    def test_first_and_missing_recent_samples_no_record_claim(self):
        now=200*DAY
        self.assertEqual(self.i.badge(offer(),'@canal',now),'')
        self.record(offer(),now-200*DAY)
        self.assertEqual(self.i.badge(offer(),'@canal',now),'')

    def test_confirmed_history_only_and_atomic_finish(self):
        o=offer();self.i.enqueue([o])
        day=self.ledger.reserve(o['product_id'])
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM price_history').fetchone()[0],0)
        self.ledger.finish(o['product_id'],day,1,offer=o,channel='@canal')
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM price_history').fetchone()[0],1)
        self.assertEqual(self.i.pending('@canal'),[])

    def test_transaction_rolls_back_on_history_failure(self):
        o=offer();day=self.ledger.reserve(o['product_id'])
        with patch.object(Intelligence,'record',side_effect=RuntimeError('disk')):
            with self.assertRaises(RuntimeError):
                self.ledger.finish(o['product_id'],day,1,offer=o,channel='@canal')
        self.assertEqual(self.ledger.db.execute('SELECT status FROM posts').fetchone()[0],'reserved')

    def test_repeat_only_lower_after_24h_and_never_uncertain(self):
        now=200*DAY
        self.record(offer(),now-DAY)
        self.assertTrue(self.i.can_repeat(offer(price='90,00'),'@canal',now))
        self.assertFalse(self.i.can_repeat(offer(),'@canal',now))
        self.assertFalse(self.i.can_repeat(offer(price='90,00'),'@canal',now-1))
        self.ledger.reserve('Shopee:1:1')
        with patch('inteligencia_ofertas.time.time',return_value=now):
            self.assertIsNone(self.ledger.reserve('Shopee:1:1',offer=offer(price='90,00'),channel='@canal'))

    def test_direct_send_timeout_never_records_price(self):
        client=Mock();client.prepare.return_value=offer()
        result=publish_one(client,self.ledger,offer(),'fake','@canal',Mock(side_effect=TimeoutError))
        self.assertEqual(result[0],'incerta')
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM price_history').fetchone()[0],0)
        self.assertIsNone(self.ledger.reserve('Shopee:1:1'))

    def test_invalid_candidate_does_not_delay_next_candidate(self):
        import bot_ofertas_revisao as publisher
        from types import SimpleNamespace
        from shopee_afiliados import AffiliateError
        a,b=offer(1),offer(2)
        self.i.enqueue([a,b])
        client=Mock()
        client.prepare.side_effect=[
            AffiliateError('Indisponível'),
            dict(b, affiliate_url='https://s.shopee.com.br/gateok',
                 affiliate_generated=True, price_from=False),
        ]
        with patch.object(publisher,'Ledger',return_value=self.ledger), \
             patch.object(publisher.ShopeeAffiliate,'from_env',return_value=client), \
             patch.object(publisher,'rows',return_value=iter([])), \
             patch.object(publisher,'send',return_value=(42,0)) as send, \
             patch.object(publisher.time,'sleep',side_effect=KeyboardInterrupt), \
             patch.dict('os.environ',{'TELEGRAM_TOKEN':'fake','TELEGRAM_CANAL':'@canal'}), \
             patch('builtins.print'):
            with self.assertRaises(KeyboardInterrupt):
                publisher.run_publisher(SimpleNamespace(simular=False),Mock())
        self.assertEqual(send.call_args.args[2]['product_id'],b['product_id'])
        self.assertGreater(self.ledger.publication_delay(clock_id=2),590)
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM price_history').fetchone()[0],1)
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM radar_queue').fetchone()[0],0)

    def test_price_and_badge_layout(self):
        text=caption(offer(history_badge='📉 Novo menor preço divulgado neste canal nos últimos 30 dias.'))
        self.assertIn('💰 <b>R$ 100,00</b>\n\n📉',text)
        self.assertEqual(price_info('🔥 Por: **R$ 2.713,08** no pix')['price'],'2.713,08')

if __name__=='__main__': unittest.main()
