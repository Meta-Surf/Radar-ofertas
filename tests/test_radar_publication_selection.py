"""Vagas de coleta pertencem a candidatos publicáveis; rede sempre simulada."""
import csv
import io
import sqlite3
import tempfile
import time
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bot_ofertas_revisao as publisher
import radar_kabum as kabum
from inteligencia_ofertas import Intelligence
from kabum_afiliados import KabumAffiliate
from ofertas_core import Ledger
from fila_ofertas_sqlite import CapturedOfferQueue
from tests.test_prepublicacao import ml_offer


def feed(prices):
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(['aw_deep_link','product_name','merchant_product_id','search_price','merchant_image_url'])
    for pid, price in prices.items():
        writer.writerow([f'https://www.awin1.com/pclick.php?p={pid}&a=3106767&m=17729',
                         f'Produto {pid}',pid,price,'https://images0.kabum.com.br/test.jpg'])
    return stream.getvalue().encode()


class RadarPublicationSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = time.time()
        self.ledger = Ledger(self.root/'publicacoes.sqlite3')
        self.addCleanup(self.ledger.db.close)
        self.intelligence = Intelligence(self.ledger.db)
        self.catalog = kabum.init_db(self.root/'kabum_historico.sqlite3')
        self.addCleanup(self.catalog.close)
        with patch.object(kabum.time,'time',return_value=self.now-3600):
            kabum.ingest(feed({str(pid):'100.00' for pid in range(1,22)}),self.catalog)
        self.current = feed({str(pid):'50.00' if pid<=20 else '80.00' for pid in range(1,22)})
        with patch.object(kabum.time,'time',return_value=self.now):
            kabum.ingest(self.current,self.catalog)
        self.pool = kabum.production_candidates(self.catalog,limit=None,now=self.now)
        self.assertEqual(len(self.pool),21)
        for offer in self.pool[:20]:
            day=self.ledger.reserve(offer['product_id'],offer=offer,channel='@audit')
            self.ledger.finish(offer['product_id'],day,int(offer['product_id'].split(':')[1]),
                               offer=offer,channel='@audit')

    def collect(self,limit=20):
        with patch.object(kabum,'resolve_feed',return_value=(self.current,'local fixture')), \
             patch('awin_kabum.AwinKabumAPI.from_env',return_value=Mock(enabled=False)), \
             patch('requests.get',side_effect=AssertionError('network forbidden')), \
             patch('requests.post',side_effect=AssertionError('network forbidden')):
            return kabum.production_round(self.root,'@audit',limit=limit)

    def test_twenty_sent_leaders_do_not_hide_the_twenty_first(self):
        old=kabum.production_candidates(self.catalog,limit=20,now=self.now)
        self.assertTrue(all(not self.intelligence.publication_eligible(o,'@audit') for o in old))
        result=self.collect()
        self.assertEqual([o['product_id'] for o in result['candidates']],['KaBuM:21'])
        self.assertEqual(result['pending'],1)
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM posts').fetchone()[0],20)

    def test_repeated_collection_is_one_queue_row_without_duplicate_posts(self):
        self.collect();self.collect()
        self.assertEqual(self.ledger.db.execute('SELECT product FROM radar_queue').fetchall(),[('KaBuM:21',)])
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM posts').fetchone()[0],20)

    def test_uncertain_candidate_does_not_take_a_slot_or_get_released(self):
        with self.ledger.db:
            self.ledger.db.execute("INSERT INTO posts(product,day,status) VALUES('KaBuM:21','2000-01-01','uncertain')")
        self.assertEqual(self.collect()['candidates'],[])
        self.assertEqual(self.ledger.db.execute("SELECT status FROM posts WHERE product='KaBuM:21'").fetchone()[0],'uncertain')

    def test_same_price_and_recent_lower_price_do_not_bypass_deduplication(self):
        old=self.pool[0]
        self.assertFalse(self.intelligence.publication_eligible(old,'@audit',self.now+86400))
        lower=dict(old,price='40,00')
        self.assertFalse(self.intelligence.publication_eligible(lower,'@audit',self.now+10))
        self.assertTrue(self.intelligence.publication_eligible(lower,'@audit',self.now+86401))

    def test_explicit_out_of_stock_and_stale_drops_remain_excluded(self):
        blocked=kabum.production_candidates(self.catalog,limit=None,availability={'21':'out_of_stock'},now=self.now)
        self.assertNotIn('KaBuM:21',[o['product_id'] for o in blocked])
        self.assertEqual(kabum.production_candidates(self.catalog,limit=None,now=self.now+2*86400),[])

    def test_default_limit_and_order_preserved_for_diagnostic_callers(self):
        self.assertEqual([o['product_id'] for o in kabum.production_candidates(self.catalog,now=self.now)],
                         [o['product_id'] for o in self.pool[:20]])
        self.assertEqual([o['radar_score'] for o in self.pool],sorted([o['radar_score'] for o in self.pool],reverse=True))

    def test_real_publisher_continues_from_failed_ml_to_kabum_without_duplicate(self):
        self.collect()
        stale=ml_offer();stale.update(name='Fixture ML',api_image='https://http2.mlstatic.com/f.jpg',chat_id=-1,message_id=1)
        queue=CapturedOfferQueue(self.root/'publicacoes.sqlite3');queue.replace_capture([stale]);queue.close()
        affiliate=Mock(cookie='fake',csrf='fake',tag='fake')
        affiliate.prepare.side_effect=lambda o:dict(o,affiliate_generated=True,affiliate_url='https://meli.la/audit')
        with ExitStack() as stack:
            stack.enter_context(patch.object(publisher,'BASE',self.root))
            stack.enter_context(patch.object(publisher,'Ledger',return_value=self.ledger))
            stack.enter_context(patch.object(publisher.MercadoLivreAffiliate,'from_env',return_value=affiliate))
            stack.enter_context(patch.object(publisher.KabumAffiliate,'from_env',return_value=KabumAffiliate(self.root/'kabum_historico.sqlite3','3106767')))
            for factory in (publisher.ShopeeAffiliate,publisher.AmazonCreators):
                stack.enter_context(patch.object(factory,'from_env',side_effect=publisher.AffiliateError('disabled')))
            reader=Mock();reader.read.side_effect=publisher.AffiliateError('external unavailable')
            stack.enter_context(patch('mercadolivre_auto.AutoReader',return_value=reader))
            get=stack.enter_context(patch('requests.get',side_effect=AssertionError('network forbidden')))
            post=stack.enter_context(patch('requests.post',side_effect=AssertionError('network forbidden')))
            sender=stack.enter_context(patch.object(publisher,'send',return_value=(77,0)))
            stack.enter_context(patch.object(publisher.ShadowDistribution,'mirror_success',return_value={}))
            stack.enter_context(patch.object(publisher.time,'sleep',side_effect=KeyboardInterrupt))
            stack.enter_context(patch.dict('os.environ',{'TELEGRAM_TOKEN':'fake','TELEGRAM_CANAL':'@audit'}))
            stack.enter_context(patch('builtins.print'))
            for iteration in range(2):
                with self.assertRaises(KeyboardInterrupt):
                    publisher.run_publisher(SimpleNamespace(simular=False),Mock())
            self.assertEqual(sender.call_count,1)
            self.assertEqual(sender.call_args.args[2]['product_id'],'KaBuM:21')
            self.assertEqual(self.ledger.db.execute("SELECT status,message_id FROM posts WHERE product='KaBuM:21'").fetchall(),[('sent',77)])
            get.assert_not_called();post.assert_not_called()
            self.assertEqual(self.ledger.db.execute("SELECT reason FROM publisher_retry WHERE retry_key=?",('captured_queue:tg:-1:1:' + stale['product_id'],)).fetchone()[0],'VALIDACAO_INDISPONIVEL')
