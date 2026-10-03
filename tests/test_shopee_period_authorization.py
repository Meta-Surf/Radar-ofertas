"""Fronteira temporal depois dos locks, sem API e sem regressão de seleção."""
import copy
import json
import os
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from inteligencia_ofertas import Intelligence
from ofertas_core import Ledger
from radar_shopee import candidate
from revisao_publicacao import selection, selection_id
from tests.test_shopee_product_period import PeriodFixture


class PeriodAuthorizationTests(PeriodFixture):
    def reserved(self):
        selected=self.selected();validated=self.validate()
        day=self.ledger.reserve(self.original['product_id'],offer=self.original,channel='@audit',selected=selected)
        self.assertIsNotNone(day);return selected,validated,day

    def authorize(self,ledger,selected,validated,day):
        return ledger.mark_sending(self.original['product_id'],day,selected=selected,
                                  shopee_period_proof=validated['_shopee_period_proof'])

    def newer(self):
        return candidate(dict(self.native,periodEndTime=self.native['periodEndTime']+600))

    def test_active_real_publisher_sends_and_does_not_leak_private_proof(self):
        self.selected();_,_,sender,_=self.run_publisher()
        self.assertEqual(sender.call_count,1);self.assertNotIn('_shopee_period_proof',sender.call_args.args[2])
        self.assertEqual(self.ledger.db.execute('SELECT state,external_id FROM deliveries').fetchall(),[('SENT','77')])

    def test_direct_mode_expired_during_preparation_never_sends(self):
        from radar_shopee_continuo import publish_one
        from unittest.mock import Mock
        self.link_hook=lambda:setattr(self,'moment',self.moment+timedelta(minutes=2))
        sender=Mock(return_value=(77,0))
        self.assertEqual(publish_one(self.client,self.ledger,self.original,'fake','@audit',sender)[0],'revalidacao_falhou')
        sender.assert_not_called();self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM posts').fetchone()[0],0)

    def test_direct_mode_expiration_after_reserve_never_sends(self):
        from radar_shopee_continuo import publish_one
        from unittest.mock import Mock
        method=self.ledger.reserve
        def reserve(*args,**kwargs):
            day=method(*args,**kwargs);self.moment+=timedelta(minutes=2);return day
        sender=Mock(return_value=(77,0))
        with patch.object(self.ledger,'reserve',side_effect=reserve):
            self.assertEqual(publish_one(self.client,self.ledger,self.original,'fake','@audit',sender)[0],'rejeitada')
        sender.assert_not_called();self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM posts').fetchone()[0],0)

    def test_direct_mode_active_confirmation_uses_same_selection_protocol(self):
        from radar_shopee_continuo import publish_one
        from unittest.mock import Mock
        sender=Mock(return_value=(77,0))
        self.assertEqual(publish_one(self.client,self.ledger,self.original,'fake','@audit',sender),('publicada',77))
        self.assertEqual(self.ledger.db.execute('SELECT state,external_id FROM deliveries').fetchall(),[('SENT','77')])
        self.assertNotIn('_shopee_period_proof',sender.call_args.args[2])

    def test_direct_mode_old_candidate_cannot_overwrite_existing_new_revision(self):
        from radar_shopee_continuo import publish_one
        from unittest.mock import Mock
        new=self.newer();self.i.enqueue([new]);sender=Mock(return_value=(77,0))
        self.assertEqual(publish_one(self.client,self.ledger,self.original,'fake','@audit',sender)[0],'revalidacao_falhou')
        sender.assert_not_called()
        self.assertEqual(json.loads(self.ledger.db.execute('SELECT payload FROM radar_queue').fetchone()[0])['shopee_offer_period'],new['shopee_offer_period'])

    def test_original_expiration_after_gate_blocks_send_and_cancels_own_reservation(self):
        self.selected()
        def advance():self.moment+=timedelta(minutes=2)
        reserve,authorize,sender,shadow=self.run_publisher(reserve_hook=advance)
        self.assertEqual((reserve.call_count,authorize.call_count),(1,1))
        sender.assert_not_called();shadow.assert_not_called()
        self.assertEqual(self.ledger.authorization_reason,'OFERTA_EXPIRADA')
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM posts').fetchone()[0],0)
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)

    def test_missing_conflicting_or_other_product_proof_never_authorizes(self):
        for proof in (None,{},dict(self.original['shopee_offer_period'],end=self.native['periodEndTime']+1),
                      dict(self.original['shopee_offer_period'],item_id='999')):
            selected,validated,day=self.reserved();validated['_shopee_period_proof']=proof
            self.assertFalse(self.authorize(self.ledger,selected,validated,day))
            self.assertEqual(self.ledger.authorization_reason,'OFERTA_VALIDADE_NAO_CONFIRMADA')
            self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM posts').fetchone()[0],0)

    def test_local_expiration_never_changes_sent_sending_uncertain_or_another_day(self):
        selected,validated,day=self.reserved();self.moment+=timedelta(minutes=2)
        with self.ledger.db:self.ledger.db.execute("INSERT INTO posts(product,day,status,message_id) VALUES(?,?,'sent',88)",(self.original['product_id'],'2000-01-01'))
        for state in ('sent','sending','uncertain'):
            with self.ledger.db:self.ledger.db.execute('UPDATE posts SET status=?,message_id=77 WHERE day=?',(state,day))
            self.assertFalse(self.authorize(self.ledger,selected,validated,day))
            self.assertEqual(self.ledger.db.execute('SELECT status,message_id FROM posts WHERE day=?',(day,)).fetchone(),(state,77))
            self.assertEqual(self.ledger.db.execute("SELECT status,message_id FROM posts WHERE day='2000-01-01'").fetchone(),('sent',88))

    def test_new_period_before_authorization_preserves_new_queue_revision(self):
        selected,validated,day=self.reserved();new=self.newer()
        with sqlite3.connect(self.path) as db:Intelligence(db).enqueue([new])
        self.assertFalse(self.authorize(self.ledger,selected,validated,day))
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM posts').fetchone()[0],0)
        self.assertEqual(json.loads(self.ledger.db.execute('SELECT payload FROM radar_queue').fetchone()[0])['shopee_offer_period'],new['shopee_offer_period'])

    def waiting_case(self,replace=False):
        selected,validated,day=self.reserved();key=self.original['product_id']
        ready=threading.Event();go=threading.Event();waiting=threading.Event();results=[];errors=[]
        def worker():
            ledger=None
            try:
                ledger=Ledger(self.path)
                ledger.db.set_trace_callback(lambda sql:waiting.set() if sql=='BEGIN IMMEDIATE' else None)
                ready.set();go.wait(3);results.append(self.authorize(ledger,selected,validated,day))
            except BaseException as error:errors.append(error);ready.set();waiting.set()
            finally:
                if ledger:ledger.db.close()
        thread=threading.Thread(target=worker,name='period-lock');thread.start()
        self.assertTrue(ready.wait(3))
        blocker=sqlite3.connect(self.path)
        new=self.newer()
        try:
            blocker.execute('BEGIN IMMEDIATE');go.set();self.assertTrue(waiting.wait(3))
            self.moment=datetime.fromtimestamp(self.native['periodEndTime'],timezone.utc)+timedelta(microseconds=1)
            if replace:
                raw=json.dumps(new,ensure_ascii=False);replacement=selection('radar_queue',key,raw)
                blocker.execute('UPDATE radar_queue SET payload=? WHERE product=?',(raw,key))
                blocker.execute('UPDATE publication_selections SET selection=? WHERE product=? AND day=?',
                                (selection_id(replacement),key,day))
                blocker.commit()
        finally:blocker.rollback();blocker.close()
        thread.join(5);self.assertFalse(thread.is_alive());self.assertEqual(errors,[]);self.assertEqual(results,[False])
        if replace:
            self.assertEqual(self.ledger.db.execute('SELECT status FROM posts').fetchone()[0],'reserved')
            raw=self.ledger.db.execute('SELECT payload FROM radar_queue').fetchone()[0]
            self.assertEqual(json.loads(raw)['shopee_offer_period'],new['shopee_offer_period'])
            self.assertEqual(self.ledger.db.execute('SELECT selection FROM publication_selections').fetchone()[0],selection_id(replacement))
        else:self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM posts').fetchone()[0],0)

    def test_independent_ledger_lock_wait_crosses_end_and_local_barrier_refuses(self):self.waiting_case()

    def test_replacement_binding_during_lock_wait_cannot_be_cancelled_by_old_selection(self):self.waiting_case(replace=True)

    def test_eight_independent_connections_authorize_only_one_active_selection(self):
        selected,validated,day=self.reserved();barrier=threading.Barrier(8)
        def worker(_):
            ledger=Ledger(self.path)
            try:barrier.wait();return self.authorize(ledger,selected,validated,day)
            finally:ledger.db.close()
        with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(worker,range(8)))
        self.assertEqual(sum(results),1)

    def test_crash_after_authorization_restart_keeps_uncertain_and_new_period(self):
        selected=self.selected();validated=self.validate()
        code='''import json,sys,time,os
from ofertas_core import Ledger
d=json.loads(sys.argv[1]);time.time=lambda:d['now']
l=Ledger(d['path']);o=d['offer'];s=d['selected']
day=l.reserve(o['product_id'],offer=o,channel='@audit',selected=s)
assert l.mark_sending(o['product_id'],day,selected=s,shopee_period_proof=d['proof'])
os._exit(19)
'''
        root=Path(__file__).resolve().parents[1]
        data=dict(now=self.moment.timestamp(),path=str(self.path),offer=self.original,selected=selected,
                  proof=validated['_shopee_period_proof'])
        proc=subprocess.run([sys.executable,'-c',code,json.dumps(data)],cwd=root,
                            env={'PYTHONPATH':str(root),'PATH':os.defpath},capture_output=True,text=True,timeout=10)
        self.assertEqual(proc.returncode,19,proc.stderr)
        self.moment+=timedelta(minutes=2);new=self.newer();self.i.enqueue([new])
        self.assertEqual(self.ledger.reconcile_reservations()['moved_to_uncertain'],1)
        raw=self.ledger.db.execute('SELECT payload FROM radar_queue').fetchone()[0]
        token=selection('radar_queue',new['product_id'],raw)
        self.assertIsNone(self.ledger.reserve(new['product_id'],offer=new,channel='@audit',selected=token))
        self.assertEqual(json.loads(raw)['shopee_offer_period'],new['shopee_offer_period'])
        self.assertEqual(self.i.pending('@audit'),[])

    def test_crash_before_authorization_releases_only_abandoned_reserved_after_restart(self):
        selected,validated,day=self.reserved()
        self.ledger.db.close();self.ledger=Ledger(self.path);self.addCleanup(self.ledger.db.close)
        self.assertEqual(self.ledger.reconcile_reservations()['released_abandoned_reserved'],1)
        self.assertFalse(self.authorize(self.ledger,selected,validated,day))
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)

    def test_confirmation_after_authorization_keeps_new_revision_and_sent_payload_idempotent(self):
        selected,validated,day=self.reserved();self.assertTrue(self.authorize(self.ledger,selected,validated,day))
        new=self.newer();self.i.enqueue([new]);key=self.original['product_id']
        self.ledger.finish(key,day,77,self.original,'@audit',selected=selected)
        self.ledger.finish(key,day,77,self.original,'@audit',selected=selected)
        self.assertEqual(json.loads(self.ledger.db.execute('SELECT payload FROM radar_queue').fetchone()[0])['shopee_offer_period'],new['shopee_offer_period'])
        old=json.loads(self.ledger.db.execute('SELECT payload FROM deliveries').fetchone()[0])
        self.assertEqual(old['shopee_offer_period'],self.original['shopee_offer_period'])
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM price_history').fetchone()[0],1)

    def test_exception_inside_local_check_rolls_back_and_preserves_original_reserved(self):
        selected,validated,day=self.reserved()
        with patch('radar_shopee.product_period_status',side_effect=RuntimeError('local failure')):
            with self.assertRaises(RuntimeError):self.authorize(self.ledger,selected,validated,day)
        self.assertEqual(self.ledger.db.execute('SELECT status FROM posts').fetchone()[0],'reserved')
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM radar_queue').fetchone()[0],1)

    def test_sqlite_authorization_failure_does_not_send_and_preserves_revision(self):
        self.selected()
        with patch.object(self.ledger,'mark_sending',side_effect=sqlite3.OperationalError('local lock failure')):
            _,_,sender,shadow=self.run_publisher()
        sender.assert_not_called();shadow.assert_not_called()
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM posts').fetchone()[0],0)
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM radar_queue').fetchone()[0],1)
