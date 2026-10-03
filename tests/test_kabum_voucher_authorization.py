import json
import os
import sqlite3
import subprocess
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from ofertas_core import Ledger
from inteligencia_ofertas import Intelligence
from revisao_publicacao import selection
from tests.test_kabum_product_voucher import VoucherFixture

class VoucherAuthorizationTests(VoucherFixture):
    def prepared_reservation(self):
        offer=self.candidate();selected=self.selected(offer);validated=self.validate(offer)
        day=self.ledger.reserve(offer['product_id'],offer=offer,channel='@audit',selected=selected)
        return offer,selected,validated,day

    def authorize(self,ledger,offer,selected,validated,day):
        return ledger.mark_sending(offer['product_id'],day,selected=selected,
            catalog_path=self.root/'kabum_historico.sqlite3',catalog_proof=validated['_catalog_revision'],
            voucher_proof=validated['_voucher_proof'])

    def test_active_authorizes_and_private_proof_is_not_passed_to_sender(self):
        self.selected(self.candidate());reserve,authorize,sender,shadow=self.run_publisher()
        self.assertEqual(sender.call_count,1);self.assertNotIn('_voucher_proof',sender.call_args.args[2])
        self.assertEqual(self.ledger.db.execute('SELECT state,external_id FROM deliveries').fetchall(),[('SENT','77')])

    def test_expiration_after_gate_cancels_only_own_reserved_without_send(self):
        self.selected(self.candidate())
        def advance():self.moment+=timedelta(minutes=2)
        reserve,authorize,sender,shadow=self.run_publisher(reserve_hook=advance)
        self.assertEqual(reserve.call_count,1);self.assertEqual(authorize.call_count,1)
        sender.assert_not_called();shadow.assert_not_called()
        self.assertEqual(self.ledger.authorization_reason,'CUPOM_EXPIRADO')
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM posts').fetchone()[0],0)
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)

    def test_new_revision_during_old_validation_is_preserved(self):
        old=self.candidate();self.selected(old)
        def change():
            self.moment+=timedelta(minutes=2)
            self.native=self.native_voucher(promotionId=888,voucher={'code':'NOVO10'})
            self.api.offers.return_value=[self.native]
            with sqlite3.connect(self.path) as db:Intelligence(db).enqueue([self.candidate()])
        _,_,sender,shadow=self.run_publisher(reserve_hook=change)
        sender.assert_not_called();shadow.assert_not_called()
        row=json.loads(self.ledger.db.execute('SELECT payload FROM radar_queue').fetchone()[0])
        self.assertEqual(row['coupon'],'NOVO10');self.assertEqual(row['kabum_voucher']['promotion_id'],'888')
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM posts').fetchone()[0],0)
        self.gate._runtime_cache.clear();self.validate(row)

    def test_expired_callback_cannot_cancel_a_replacement_reservation_binding(self):
        offer,selected,validated,day=self.prepared_reservation()
        self.moment+=timedelta(minutes=2);self.native=self.native_voucher(promotionId=888,voucher={'code':'NOVO10'})
        new=self.candidate()
        with sqlite3.connect(self.path) as db:
            Intelligence(db).enqueue([new])
            raw=db.execute('SELECT payload FROM radar_queue').fetchone()[0]
            token=selection('radar_queue',new['product_id'],raw)
            db.execute('UPDATE publication_selections SET selection=?',
                       (json.dumps(token,sort_keys=True,separators=(',',':')),))
        self.assertFalse(self.authorize(self.ledger,offer,selected,validated,day))
        self.assertEqual(self.ledger.db.execute('SELECT status FROM posts').fetchone()[0],'reserved')
        self.assertEqual(json.loads(self.ledger.db.execute('SELECT payload FROM radar_queue').fetchone()[0])['coupon'],'NOVO10')

    def test_expired_voucher_never_alters_sent_sending_or_uncertain(self):
        offer,selected,validated,day=self.prepared_reservation();self.moment+=timedelta(minutes=2)
        for state in ('sent','sending','uncertain'):
            with self.subTest(state=state),sqlite3.connect(self.path) as db:
                db.execute('UPDATE posts SET status=?,message_id=77',(state,))
            self.assertFalse(self.authorize(self.ledger,offer,selected,validated,day))
            self.assertEqual(self.ledger.db.execute('SELECT status,message_id FROM posts').fetchone(),(state,77))

    def test_missing_or_wrong_proof_is_not_authorization(self):
        offer,selected,validated,day=self.prepared_reservation()
        self.assertFalse(self.ledger.mark_sending(offer['product_id'],day,selected=selected,
            catalog_path=self.root/'kabum_historico.sqlite3',catalog_proof=validated['_catalog_revision']))
        self.assertEqual(self.ledger.authorization_reason,'CUPOM_VALIDADE_NAO_CONFIRMADA')

    def wait_across_deadline(self,which):
        offer,selected,validated,day=self.prepared_reservation()
        ready=threading.Event();go=threading.Event();waiting=threading.Event();result=[];errors=[]
        connect=sqlite3.connect
        def traced_connect(path,*args,**kwargs):
            db=connect(path,*args,**kwargs)
            if threading.current_thread().name=='voucher-lock' and 'kabum_historico' in str(path):
                db.set_trace_callback(lambda sql:waiting.set() if sql=='BEGIN IMMEDIATE' else None)
            return db
        def worker():
            ledger=None
            try:
                ledger=Ledger(self.path)
                if which=='ledger':ledger.db.set_trace_callback(lambda sql:waiting.set() if sql=='BEGIN IMMEDIATE' else None)
                ready.set();go.wait(3)
                result.append(self.authorize(ledger,offer,selected,validated,day))
            except BaseException as error:errors.append(error);ready.set();waiting.set()
            finally:
                if ledger:ledger.db.close()
        thread=threading.Thread(target=worker,name='voucher-lock')
        with patch('revisao_publicacao.sqlite3.connect',side_effect=traced_connect):
            thread.start();self.assertTrue(ready.wait(3))
            blocker=connect(self.path if which=='ledger' else self.root/'kabum_historico.sqlite3')
            try:
                blocker.execute('BEGIN IMMEDIATE');go.set();self.assertTrue(waiting.wait(3))
                self.moment=datetime.fromisoformat(offer['kabum_voucher']['end_date'])+timedelta(microseconds=1)
            finally:blocker.rollback();blocker.close()
            thread.join(5)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[]);self.assertEqual(result,[False])
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM posts').fetchone()[0],0)
        self.api.offers.assert_called_once()

    def test_ledger_lock_wait_is_followed_by_new_local_deadline_check(self):self.wait_across_deadline('ledger')

    def test_catalog_lock_wait_is_followed_by_new_local_deadline_check(self):self.wait_across_deadline('catalog')

    def test_eight_connections_authorize_only_one_active_reservation(self):
        offer,selected,validated,day=self.prepared_reservation();barrier=threading.Barrier(8)
        def worker(_):
            ledger=Ledger(self.path)
            try:barrier.wait();return self.authorize(ledger,offer,selected,validated,day)
            finally:ledger.db.close()
        with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(worker,range(8)))
        self.assertEqual(sum(results),1)

    def test_real_crash_after_authorization_restart_keeps_uncertain_and_new_revision(self):
        offer=self.candidate();selected=self.selected(offer);validated=self.validate(offer)
        code='''import json,sys,time,os
from ofertas_core import Ledger
data=json.loads(sys.argv[1]);time.time=lambda:data['now']
l=Ledger(data['path']);o=data['offer'];s=data['selected'];v=data['validated']
d=l.reserve(o['product_id'],offer=o,channel='@audit',selected=s)
assert l.mark_sending(o['product_id'],d,selected=s,catalog_path=data['catalog'],catalog_proof=v['_catalog_revision'],voucher_proof=v['_voucher_proof'])
os._exit(19)
'''
        data=dict(path=str(self.path),catalog=str(self.root/'kabum_historico.sqlite3'),offer=offer,
                  selected=selected,validated=validated,now=self.moment.timestamp())
        isolated=Path(__file__).resolve().parents[1]
        proc=subprocess.run([sys.executable,'-c',code,json.dumps(data)],cwd=isolated,
                            env={'PYTHONPATH':str(isolated),'PATH':os.defpath},capture_output=True,text=True,timeout=10)
        self.assertEqual(proc.returncode,19,proc.stderr)
        self.moment+=timedelta(minutes=2);self.native=self.native_voucher();new=self.candidate()
        self.i.enqueue([new]);self.assertEqual(self.ledger.reconcile_reservations()['moved_to_uncertain'],1)
        raw=self.ledger.db.execute('SELECT payload FROM radar_queue').fetchone()[0]
        token=selection('radar_queue',new['product_id'],raw)
        self.assertIsNone(self.ledger.reserve(new['product_id'],offer=new,channel='@audit',selected=token))
        self.assertEqual(json.loads(raw)['kabum_voucher']['revision_digest'],new['kabum_voucher']['revision_digest'])
        self.assertEqual(self.i.pending('@audit'),[])

    def test_edit_after_authorization_keeps_actual_confirmation_and_new_voucher(self):
        offer,selected,validated,day=self.prepared_reservation()
        self.assertTrue(self.authorize(self.ledger,offer,selected,validated,day))
        self.moment+=timedelta(minutes=2);self.native=self.native_voucher(promotionId=888,voucher={'code':'NOVO10'})
        new=self.candidate();self.i.enqueue([new])
        self.ledger.finish(offer['product_id'],day,77,offer,'@audit',selected=selected)
        self.assertEqual(json.loads(self.ledger.db.execute('SELECT payload FROM radar_queue').fetchone()[0])['coupon'],'NOVO10')
        self.assertEqual(self.i.pending('@audit'),[])
        payload=json.loads(self.ledger.db.execute('SELECT payload FROM deliveries').fetchone()[0])
        self.assertEqual(payload['coupon'],'TESTE10')
        self.ledger.finish(offer['product_id'],day,77,offer,'@audit',selected=selected)
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM price_history').fetchone()[0],1)
