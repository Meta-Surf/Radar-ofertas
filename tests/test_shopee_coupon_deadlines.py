"""Relógio fixo, rede simulada e SQLite temporário para validade comercial."""
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bot_ofertas_revisao as publisher
import cupons_shopee as coupons
from fila_ofertas_sqlite import CapturedOfferQueue
from ofertas_core import Ledger
from prepublicacao import GateReject, PrePublicationGate

TZ = coupons.COMMERCIAL_TZ
NOW = datetime(2026, 10, 3, 10, tzinfo=TZ)


def alert(*conditions):
    entries = [dict(url=f'https://shopee.com.br/m/coupon-{i}', conditions=text,
                    affiliate_url=f'https://s.shopee.com.br/audit{i}')
               for i, text in enumerate(conditions)]
    return dict(kind='coupon_alert', source='telegram', store='Shopee', entries=entries,
        product_id=coupons.alert_key(entries, NOW.isoformat()), source_date=NOW.isoformat(),
        chat_id=-1, message_id=1, affiliate_generated=True)


class DeadlineParserTests(unittest.TestCase):
    def test_expired_yesterday_and_days_ago(self):
        for date in ('02/10/2026', '01/10/2026', '01/09/2026'):
            self.assertEqual(coupons.deadline_status('Válido até '+date+' às 23:59', NOW), 'expired')

    def test_future_and_today_before_limit(self):
        for date in ('04/10/2026', '03/10/2026'):
            self.assertEqual(coupons.deadline_status('Válido até '+date+' às 23:59', NOW), 'active')

    def test_exact_limit_is_exclusive_and_after_limit_expired(self):
        deadline = datetime(2026, 10, 1, 23, 59, tzinfo=TZ)
        text = 'Válido até 01/10/2026 às 23:59'
        self.assertEqual(coupons.deadline_status(text, deadline-timedelta(minutes=1)), 'active')
        self.assertEqual(coupons.deadline_status(text, deadline), 'expired')
        self.assertEqual(coupons.deadline_status(text, deadline+timedelta(microseconds=1)), 'expired')
        self.assertEqual(coupons.deadline_status(text, deadline+timedelta(minutes=1)), 'expired')

    def test_date_without_time_includes_entire_commercial_day(self):
        text = 'Válido até 01/10/2026'
        end = datetime(2026, 10, 2, tzinfo=TZ)
        self.assertEqual(coupons.deadline_status(text, end-timedelta(microseconds=1)), 'active')
        self.assertEqual(coupons.deadline_status(text, end), 'expired')

    def test_server_timezone_does_not_determine_deadline(self):
        from datetime import timezone
        utc = datetime(2026, 10, 2, 2, 58, tzinfo=timezone.utc)
        self.assertEqual(coupons.deadline_status('Válido até 01/10/2026 23:59', utc), 'active')
        with self.assertRaises(ValueError):
            coupons.deadline_status('Válido até 01/10/2026', datetime(2026, 10, 1))

    def test_supported_labels_case_and_wrapped_whitespace(self):
        for text in ('Válido até 01/10/2026 às 23:59', 'válido até 01/10/2026 23:59',
                     'EXPIRA EM 01/10/2026 às 23:59', 'expira 01/10/2026',
                     'VALIDADE: 01/10/2026', 'VÁLIDO  ATÉ\n01/10/2026\nàs 23:59',
                     'Válido até 01/10/2026 23:59 (horário de Brasília)'):
            with self.subTest(text=text):
                self.assertEqual(coupons.deadline_status(text, NOW), 'expired')

    def test_money_percent_numeric_code_and_installments_are_not_deadlines(self):
        for text in ('mínimo R$ 100', '20% OFF', 'código 0110', '10 parcelas', '',
                     'válido para compras acima de R$100'):
            self.assertEqual(coupons.deadline_status(text, NOW), 'unspecified')

    def test_explicit_unparseable_invalid_date_or_unknown_year_is_unknown(self):
        for text in ('validade não informada', 'expira amanhã', 'Válido até 01/10 às 23:59',
                     'Válido até 31/02/2026', 'Válido até 04/10/2026 às XX',
                     'Válido até 04/10/2026 23:59 EST', 'expiração: depois do estoque', 'cupom expirado'):
            self.assertEqual(coupons.deadline_status(text, NOW), 'unknown')


class CouponGateDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE posts(product TEXT,day TEXT,status TEXT,message_id INTEGER)')
        self.gate = PrePublicationGate(self.temp.name, self.db)
        clock = patch('prepublicacao.datetime', wraps=datetime).start()
        clock.now.return_value = NOW
        self.addCleanup(patch.stopall)

    def validate(self, offer):
        return self.gate.validate(offer, dict(offer), '@audit')

    def test_original_reproduction_blocks_even_with_valid_affiliate_link(self):
        with self.assertRaises(GateReject) as caught:
            self.validate(alert('Válido até 01/10/2026 às 23:59'))
        self.assertEqual(caught.exception.reason, 'CUPOM_EXPIRADO')
        self.assertTrue(caught.exception.discard)
        self.assertEqual(self.db.execute('SELECT status,reason FROM prepublication_gate').fetchall(),
                         [('BLOQUEADA', 'CUPOM_EXPIRADO')])

    def test_future_single_and_no_explicit_deadline_keep_current_policy(self):
        for condition in ('Válido até 04/10/2026', '20% OFF acima de R$100'):
            result = self.validate(alert(condition))
            self.assertEqual(len(result['entries']), 1)

    def test_unconfirmed_deadline_uses_retry_not_permanent_discard(self):
        with self.assertRaises(GateReject) as caught:
            self.validate(alert('validade: data não informada'))
        self.assertEqual(caught.exception.reason, 'CUPOM_VALIDADE_NAO_CONFIRMADA')
        self.assertFalse(caught.exception.discard)
        self.assertEqual(caught.exception.retry_after, 300)

    def test_multiple_valid_entries_remain_independent(self):
        offer = alert('Válido até 04/10/2026', 'Válido até 05/10/2026')
        self.assertEqual(self.validate(offer)['entries'], offer['entries'])

    def test_all_expired_entries_block(self):
        with self.assertRaises(GateReject) as caught:
            self.validate(alert('Válido até 01/10/2026', 'expira 02/10/2026'))
        self.assertEqual(caught.exception.reason, 'CUPOM_EXPIRADO')

    def test_mixed_list_filters_expired_and_recomputes_identity_without_mutating_original(self):
        offer = alert('Válido até 01/10/2026', 'Válido até 05/10/2026')
        result = self.validate(offer)
        self.assertEqual(result['entries'], [offer['entries'][1]])
        self.assertNotEqual(result['product_id'], offer['product_id'])
        self.assertEqual(result['product_id'], coupons.alert_key(result['entries'], offer['source_date']))
        self.assertEqual(len(offer['entries']), 2)

    def test_mixed_valid_unknown_never_publishes_unknown_entry(self):
        offer = alert('Válido até 05/10/2026', 'expira amanhã')
        self.assertEqual(self.validate(offer)['entries'], [offer['entries'][0]])

    def test_conditions_are_attached_to_the_correct_url(self):
        message = SimpleNamespace(raw_text='CUPONS SHOPEE\nVálido até 01/10/2026\n'
            'https://shopee.com.br/m/a\n\nVálido até 05/10/2026\nhttps://shopee.com.br/m/b',
            date=NOW, id=1, reply_markup=None, get_entities_text=lambda: [])
        original = coupons.build_alerts([message], -1)[0]
        client = Mock()
        client.generate_coupon_link.side_effect = ['https://s.shopee.com.br/a', 'https://s.shopee.com.br/b']
        prepared = coupons.prepare_alert(client, original)
        result = self.gate.validate(original, prepared, '@audit')
        self.assertEqual(result['entries'][0]['url'], 'https://shopee.com.br/m/b')
        self.assertIn('05/10/2026', result['entries'][0]['conditions'])
        self.assertEqual(client.generate_coupon_link.call_count, 2)

    def test_shared_conditions_on_same_url_line_apply_to_both_entries(self):
        message = SimpleNamespace(raw_text='CUPONS SHOPEE\nVálido até 01/10/2026\n'
            'https://shopee.com.br/m/a https://shopee.com.br/m/b',date=NOW,id=1,
            reply_markup=None,get_entities_text=lambda: [])
        entries = coupons.build_alerts([message], -1)[0]['entries']
        self.assertEqual(len(entries), 2)
        self.assertTrue(all('01/10/2026' in entry['conditions'] for entry in entries))

    def test_new_revision_with_future_deadline_is_reconsidered(self):
        with self.assertRaises(GateReject):
            self.validate(alert('Válido até 01/10/2026'))
        result = self.validate(alert('Válido até 05/10/2026'))
        self.assertEqual(len(result['entries']), 1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM posts').fetchone()[0], 0)


class CouponPublisherDeadlineTests(unittest.TestCase):
    def test_real_publisher_never_reserves_sends_or_shadows_expired_alert(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            ledger = Ledger(root / 'publicacoes.sqlite3')
            stack.callback(ledger.db.close)
            offer = alert('Válido até 01/10/2026 às 23:59')
            queue = CapturedOfferQueue(root / 'publicacoes.sqlite3')
            queue.replace_capture([offer], now=NOW.timestamp())
            stack.callback(queue.close)
            affiliate = Mock()
            affiliate.generate_coupon_link.return_value = 'https://s.shopee.com.br/converted'
            stack.enter_context(patch.object(publisher, 'BASE', root))
            stack.enter_context(patch.object(publisher, 'Ledger', return_value=ledger))
            stack.enter_context(patch.object(publisher, 'ordered_rows', return_value=iter([offer])))
            stack.enter_context(patch.object(publisher.ShopeeAffiliate, 'from_env', return_value=affiliate))
            for factory in (publisher.MercadoLivreAffiliate, publisher.KabumAffiliate, publisher.AmazonCreators):
                stack.enter_context(patch.object(factory, 'from_env', side_effect=publisher.AffiliateError('disabled locally')))
            stack.enter_context(patch('mercadolivre_auto.AutoReader', return_value=Mock()))
            for target in ('bot_ofertas_revisao.datetime', 'prepublicacao.datetime'):
                clock = stack.enter_context(patch(target, wraps=datetime))
                clock.now.return_value = NOW
            stack.enter_context(patch('requests.get', side_effect=AssertionError('unexpected network')))
            stack.enter_context(patch('requests.post', side_effect=AssertionError('unexpected network')))
            reserve = stack.enter_context(patch.object(ledger, 'reserve', wraps=ledger.reserve))
            sending = stack.enter_context(patch.object(ledger, 'mark_sending', wraps=ledger.mark_sending))
            send = stack.enter_context(patch.object(publisher, 'send_alert'))
            shadow = stack.enter_context(patch.object(publisher.ShadowDistribution, 'mirror_success'))
            stack.enter_context(patch.object(publisher.time, 'sleep', side_effect=KeyboardInterrupt))
            stack.enter_context(patch.dict('os.environ', {'TELEGRAM_TOKEN':'fake','TELEGRAM_CANAL':'@audit'}))
            stack.enter_context(patch('builtins.print'))
            with self.assertRaises(KeyboardInterrupt):
                publisher.run_publisher(SimpleNamespace(simular=False), Mock())
            reserve.assert_not_called()
            sending.assert_not_called()
            send.assert_not_called()
            shadow.assert_not_called()
            self.assertEqual(ledger.db.execute('SELECT status,reason FROM prepublication_gate').fetchall(),
                             [('BLOQUEADA', 'CUPOM_EXPIRADO')])
            self.assertEqual(ledger.db.execute('SELECT count(*) FROM posts').fetchone()[0], 0)
