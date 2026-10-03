"""Validade ML: texto global, condições por código e associação incerta."""
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bot_ofertas_revisao as publisher
import cupons_mercadolivre as coupons
from cupons_shopee import COMMERCIAL_TZ
from ofertas_core import Ledger
from prepublicacao import GateReject, PrePublicationGate

NOW = datetime(2026, 10, 3, 10, tzinfo=COMMERCIAL_TZ)


def alert(body):
    return coupons.build_alert([SimpleNamespace(raw_text='CUPONS MERCADO LIVRE\n'+body,
        date=NOW, id=1)], -1)


class MLCouponDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE posts(product TEXT,day TEXT,status TEXT,message_id INTEGER)')
        self.gate = PrePublicationGate(self.temp.name, self.db)
        self.clock = patch('prepublicacao.datetime', wraps=datetime).start()
        self.clock.now.return_value = NOW
        self.addCleanup(patch.stopall)

    def validate(self, body):
        offer = alert(body)
        self.assertIsNotNone(offer)
        return self.gate.validate(offer, coupons.prepare_alert(offer), '@audit')

    def reject(self, body, reason):
        with self.assertRaises(GateReject) as caught:
            self.validate(body)
        self.assertEqual(caught.exception.reason, reason)
        return caught.exception

    def test_original_global_expired_reproduction(self):
        error = self.reject('10% OFF em selecionados: TESTE10\nVálido até 01/10/2026 às 23:59', 'CUPOM_EXPIRADO')
        self.assertTrue(error.discard)
        self.assertEqual(error.retry_after, 0)
        self.assertEqual(self.db.execute('SELECT status,reason FROM prepublication_gate').fetchall(),
                         [('BLOQUEADA', 'CUPOM_EXPIRADO')])

    def test_global_future_after_entries_is_preserved(self):
        result = self.validate('10% OFF: TESTEA\n15% OFF: TESTEB\nVálido até 05/10/2026')
        self.assertEqual(len(result['entries']), 2)
        self.assertIn('Válido até 05/10/2026', result['text'])

    def test_global_before_entries_is_not_reassigned(self):
        self.reject('Válido até 01/10/2026\n10% OFF: TESTEA\n15% OFF: TESTEB', 'CUPOM_EXPIRADO')
        self.assertEqual(len(self.validate('Válido até 05/10/2026\n10% OFF: TESTEA\n15% OFF: TESTEB')['entries']), 2)

    def test_global_between_entries_is_ambiguous_not_only_the_previous_entry(self):
        error = self.reject('10% OFF: TESTEA\nVálido até 01/10/2026\n15% OFF: TESTEB',
                            'CUPOM_VALIDADE_NAO_CONFIRMADA')
        self.assertFalse(error.discard)
        self.assertEqual(error.retry_after, 300)

    def test_global_today_before_exact_exclusive_limit(self):
        body = '10% OFF: TESTEA\nVálido até 01/10/2026 às 23:59'
        deadline = datetime(2026, 10, 1, 23, 59, tzinfo=COMMERCIAL_TZ)
        # Captura acompanha o relógio; o prazo comercial continua independente.
        offer = alert(body)
        offer['source_date'] = deadline.isoformat()
        self.clock.now.return_value = deadline-timedelta(seconds=1)
        self.gate.validate(offer, coupons.prepare_alert(offer), '@audit')
        for moment in (deadline, deadline+timedelta(microseconds=1)):
            self.clock.now.return_value = moment
            with self.assertRaises(GateReject) as caught:
                self.gate.validate(offer, coupons.prepare_alert(offer), '@audit')
            self.assertEqual(caught.exception.reason, 'CUPOM_EXPIRADO')

    def test_date_without_time_and_utc_clock_keep_commercial_day(self):
        offer = alert('10% OFF: TESTEA\nVálido até 01/10/2026')
        offer['source_date'] = '2026-10-01T23:59:00-03:00'
        self.clock.now.return_value = datetime(2026,10,2,2,59,59,tzinfo=timezone.utc)
        self.gate.validate(offer, coupons.prepare_alert(offer), '@audit')
        self.clock.now.return_value = datetime(2026,10,2,3,tzinfo=timezone.utc)
        with self.assertRaises(GateReject) as caught:
            self.gate.validate(offer, coupons.prepare_alert(offer), '@audit')
        self.assertEqual(caught.exception.reason, 'CUPOM_EXPIRADO')

    def test_unknown_global_deadline_uses_backoff(self):
        for condition in ('validade: não informada', 'Válido até 01/10 às 23:59', 'expira amanhã'):
            self.reject('10% OFF: TESTEA\n'+condition, 'CUPOM_VALIDADE_NAO_CONFIRMADA')

    def test_no_deadline_money_percent_codes_quantity_limits_are_not_dates(self):
        result = self.validate('10% OFF mínimo R$100 limite 2 usos em 3 parcelas ID 0110: CUPOM10')
        self.assertEqual(result['entries'][0]['code'], 'CUPOM10')

    def test_inline_expired_and_future(self):
        self.reject('10% OFF até 01/10/2026 23:59: CUPOM10', 'CUPOM_EXPIRADO')
        self.assertEqual(len(self.validate('10% OFF até 05/10/2026 23:59: CUPOM10')['entries']), 1)

    def test_two_inline_valid(self):
        result = self.validate('10% OFF até 05/10/2026: CUPOM10\n20% OFF até 06/10/2026: CUPOM20')
        self.assertEqual(len(result['entries']), 2)

    def test_two_inline_expired(self):
        self.reject('10% OFF até 01/10/2026: CUPOM10\n20% OFF até 02/10/2026: CUPOM20', 'CUPOM_EXPIRADO')

    def test_mixed_inline_filters_whole_line_without_mutating_original(self):
        offer = alert('10% OFF em tênis até 05/10/2026: CUPOM10\n20% OFF em tablets até 01/10/2026: CUPOM20')
        before = dict(offer)
        result = self.gate.validate(offer, coupons.prepare_alert(offer), '@audit')
        self.assertEqual([entry['code'] for entry in result['entries']], ['CUPOM10'])
        self.assertNotIn('CUPOM20', result['text'])
        self.assertNotIn('tablets', result['text'])
        self.assertEqual(offer, before)
        self.assertNotEqual(result['product_id'], offer['product_id'])
        self.assertEqual(result['product_id'], coupons.alert_key(result['entries'], offer['source_date'], result['text']))

    def test_mixed_inline_unknown_is_excluded(self):
        result = self.validate('10% OFF até 05/10/2026: CUPOM10\n20% OFF expira amanhã: CUPOM20')
        self.assertNotIn('CUPOM20', result['text'])

    def test_none_eligible_with_unknown_is_not_permanent_discard(self):
        error = self.reject('10% OFF até 01/10/2026: CUPOM10\n20% OFF expira amanhã: CUPOM20',
                            'CUPOM_VALIDADE_NAO_CONFIRMADA')
        self.assertFalse(error.discard)

    def test_global_expired_blocks_multiple_codes(self):
        self.reject('10% OFF: TESTEA\n15% OFF: TESTEB\nVálido até 01/10/2026 23:59', 'CUPOM_EXPIRADO')

    def test_global_valid_with_individual_expired_preserves_global_qualifier(self):
        result = self.validate('10% OFF: CUPOM10\n20% OFF até 01/10/2026: CUPOM20\nVálido até 05/10/2026')
        self.assertNotIn('CUPOM20', result['text'])
        self.assertIn('Válido até 05/10/2026', result['text'])

    def test_external_reference_to_specific_code_is_ambiguous(self):
        self.reject('10% OFF: CUPOM10\n20% OFF: CUPOM20\nCUPOM20 válido até 05/10/2026',
                    'CUPOM_VALIDADE_NAO_CONFIRMADA')

    def test_separate_conditions_of_removed_code_are_not_left_in_output(self):
        self.reject('10% OFF: CUPOM10\n20% OFF até 01/10/2026: CUPOM20\nCUPOM20 apenas para novos clientes',
                    'CUPOM_VALIDADE_NAO_CONFIRMADA')

    def test_unscoped_separate_condition_cannot_be_reassigned_after_filtering(self):
        self.reject('10% OFF: CUPOM10\n20% OFF até 01/10/2026: CUPOM20\nApenas para novos clientes',
                    'CUPOM_VALIDADE_NAO_CONFIRMADA')
        result = self.validate('10% OFF: CUPOM10\n20% OFF até 01/10/2026: CUPOM20\nCondições gerais: mínimo R$100')
        self.assertIn('Condições gerais: mínimo R$100', result['text'])
        self.assertNotIn('CUPOM20', result['text'])

    def test_formats_case_whitespace_and_wrapped_label(self):
        for condition in ('Válido até 01/10/2026 às 23:59', 'válido até 01/10/2026 23:59',
                          'EXPIRA EM 01/10/2026 às 23:59', 'Expira 01/10/2026',
                          'Validade: 01/10/2026', 'Válido até\n01/10/2026 às 23:59'):
            self.reject('10% OFF: CUPOM10\n'+condition, 'CUPOM_EXPIRADO')

    def test_new_revision_future_after_expired_is_reconsidered(self):
        old = alert('10% OFF: CUPOM10\nVálido até 01/10/2026')
        with self.assertRaises(GateReject):
            self.gate.validate(old, coupons.prepare_alert(old), '@audit')
        new = alert('10% OFF: CUPOM10\nVálido até 05/10/2026')
        self.assertNotEqual(old['product_id'], new['product_id'])
        self.gate.validate(new, coupons.prepare_alert(new), '@audit')
        self.assertEqual(self.db.execute('SELECT count(*) FROM posts').fetchone()[0], 0)


class MLCouponPublisherTests(unittest.TestCase):
    def test_real_publisher_blocks_expired_global_without_reserve_send_or_shadow(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            ledger = Ledger(root / 'publicacoes.sqlite3')
            stack.callback(ledger.db.close)
            offer = alert('10% OFF em selecionados: TESTE10\nVálido até 01/10/2026 às 23:59')
            stack.enter_context(patch.object(publisher, 'BASE', root))
            stack.enter_context(patch.object(publisher, 'Ledger', return_value=ledger))
            stack.enter_context(patch.object(publisher, 'ordered_rows', return_value=iter([offer])))
            for factory in (publisher.ShopeeAffiliate,publisher.MercadoLivreAffiliate,publisher.KabumAffiliate,publisher.AmazonCreators):
                stack.enter_context(patch.object(factory, 'from_env', side_effect=publisher.AffiliateError('disabled locally')))
            stack.enter_context(patch('mercadolivre_auto.AutoReader', return_value=Mock()))
            for target in ('bot_ofertas_revisao.datetime','prepublicacao.datetime'):
                clock = stack.enter_context(patch(target, wraps=datetime))
                clock.now.return_value = NOW
            get = stack.enter_context(patch('requests.get', side_effect=AssertionError('unexpected network')))
            post = stack.enter_context(patch('requests.post', side_effect=AssertionError('unexpected network')))
            reserve = stack.enter_context(patch.object(ledger, 'reserve', wraps=ledger.reserve))
            sending = stack.enter_context(patch.object(ledger, 'mark_sending', wraps=ledger.mark_sending))
            send = stack.enter_context(patch.object(publisher.ml_coupons, 'send_alert'))
            shadow = stack.enter_context(patch.object(publisher.ShadowDistribution, 'mirror_success'))
            stack.enter_context(patch.object(publisher.time, 'sleep', side_effect=KeyboardInterrupt))
            stack.enter_context(patch.dict('os.environ', {'TELEGRAM_TOKEN':'fake','TELEGRAM_CANAL':'@audit'}))
            stack.enter_context(patch('builtins.print'))
            with self.assertRaises(KeyboardInterrupt):
                publisher.run_publisher(SimpleNamespace(simular=False), Mock())
            for mock in (reserve,sending,send,shadow,get,post):
                mock.assert_not_called()
            self.assertEqual(ledger.db.execute('SELECT status,reason FROM prepublication_gate').fetchall(),
                             [('BLOQUEADA','CUPOM_EXPIRADO')])
            self.assertEqual(ledger.db.execute('SELECT count(*) FROM posts').fetchone()[0], 0)
