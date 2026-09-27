import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import bot_ofertas_revisao as publisher
from monitor_ofertas import edited_messages
from ofertas_core import caption, price_info
from shopee_afiliados import AffiliateError


class PriceEditTests(unittest.TestCase):
    def test_screenshot_price_and_payment_condition(self):
        text = ('Ar Condicionado Split Hi Wall WindFree Ai Pro Samsung Inverter 24.000 Btus Frio 220v\n'
                '🔥 Por: R$ 2.713,08 no pix\n'
                '🛒 Link: https://s.shopee.com.br/5LCEowlqfg\n'
                '⚠️ Pode ficar mais barato se tiver cupom na conta.')
        info = price_info(text)
        self.assertEqual(info['price'], '2.713,08')
        rendered = caption(dict(info, store='Shopee'))
        self.assertIn('💰 <b>R$ 2.713,08</b>\n\nno pix', rendered)

    def test_latest_edit_replaces_missing_price_and_price_removal(self):
        old = dict(chat_id=1, message_id=2, price=None)
        new = dict(old, price='2.713,08')
        for rows, expected in [([old, new], new), ([new, old], old)]:
            with patch.object(publisher, 'rows', return_value=iter(rows)):
                self.assertEqual(list(publisher.ordered_rows()), [expected])

    def test_no_price_does_not_reserve_or_send_then_edit_can_publish(self):
        old = dict(product_id='Shopee:1:2', url='https://shopee.com.br/product/1/2',
                   source='telegram', source_date=datetime.now(timezone.utc).isoformat(),
                   chat_id=1, message_id=2, price=None, api_image='https://x.susercontent.com/a.jpg')
        new = dict(old, price='2.713,08')
        ledger = Mock()
        ledger.publication_delay.return_value = 0
        client = Mock()
        client.prepare.side_effect = lambda o: dict(o)
        with patch.object(publisher, 'rows', side_effect=[iter([old]), iter([old, new])]), \
             patch.object(publisher, 'Ledger', return_value=ledger), \
             patch.object(publisher.ShopeeAffiliate, 'from_env', return_value=client), \
             patch.object(publisher, 'send', return_value=(7, 0)) as send, \
             patch.object(publisher.time, 'sleep', side_effect=[None, KeyboardInterrupt]), \
             patch.dict('os.environ', {'TELEGRAM_TOKEN': 'fake', 'TELEGRAM_CANAL': '@fake'}), \
             patch('builtins.print'):
            with self.assertRaises(KeyboardInterrupt):
                publisher.run_publisher(SimpleNamespace(simular=False), Mock())
        ledger.reserve.assert_called_once_with('Shopee:1:2')
        client.prepare.assert_called_once()
        send.assert_called_once()
        self.assertEqual(send.call_args.args[2]['price'], '2.713,08')

    def test_send_blocks_invalid_prices_before_network(self):
        with patch.object(publisher.requests, 'post') as post:
            for value in [None, '', '0,00', 'NaN', '100,00 no pix', '1,234']:
                with self.subTest(value=value), self.assertRaises(AffiliateError):
                    publisher.send('fake', '@fake', dict(price=value, affiliate_generated=True,
                                   affiliate_url='https://s.shopee.com.br/novo'), None)
            post.assert_not_called()


class EditedMessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_message_uses_edited_caption(self):
        message = SimpleNamespace(id=10, grouped_id=None, raw_text='Por R$ 100')
        client = Mock()
        self.assertEqual(await edited_messages(client, SimpleNamespace(message=message)), [message])
        client.get_messages.assert_not_called()

    async def test_album_keeps_only_its_messages_and_latest_edit(self):
        edited = SimpleNamespace(id=11, grouped_id=70, raw_text='R$ 2.713,08')
        photo = SimpleNamespace(id=10, grouped_id=70)
        stale = SimpleNamespace(id=11, grouped_id=70, raw_text='')
        unrelated = SimpleNamespace(id=12, grouped_id=80)
        client = SimpleNamespace(get_messages=AsyncMock(return_value=[unrelated, stale, None, photo]))
        event = SimpleNamespace(message=edited, get_input_chat=AsyncMock(return_value=1))
        self.assertEqual(await edited_messages(client, event), [photo, edited])
