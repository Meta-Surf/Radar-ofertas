import asyncio
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import bot_ofertas_revisao as publisher
import mercadolivre_auto as auto
import mercadolivre_manual as manual
import monitor_ofertas as monitor
from ofertas_core import Ledger, price_info
from shopee_afiliados import AffiliateError
from fila_ofertas_sqlite import CapturedOfferQueue

SHORT = 'https://meli.la/1Xinc4n'
DIRECT = 'https://www.mercadolivre.com.br/ar-condicionado/p/MLB123456'
CANONICAL = 'https://www.mercadolivre.com.br/p/MLB123456'
IMAGE = 'https://http2.mlstatic.com/D_NQ_NP_TEST-O.webp'

ML_TEXT = '''MERCADO LIVRE

Ar-Condicionado Split Hi Wall Inverter Gree G-Side 9.000BTU Quente e Frio 220v

R$ 1.981 em 10x s/ juros

https://meli.la/1Xinc4n

Cupom: HOJEVA1 ou BARATINHO
'''

DATA = {
    '@context': 'https://schema.org',
    '@type': 'Product',
    'name': 'Ar-Condicionado Gree 9000 BTU',
    'url': DIRECT,
    'image': [IMAGE],
    'offers': {
        '@type': 'Offer',
        'price': '1981.00',
        'priceCurrency': 'BRL',
        'availability': 'https://schema.org/InStock',
    },
}


def document():
    return '<script type="application/ld+json">' + json.dumps(DATA) + '</script>'


def message(text=ML_TEXT):
    return SimpleNamespace(
        id=321,
        raw_text=text,
        photo=SimpleNamespace(id=777),
        document=None,
        grouped_id=None,
        date=datetime.now(timezone.utc),
        get_entities_text=lambda: [],
        reply_markup=None,
        noforwards=False,
    )


class MonitoredMercadoLivrePendingTests(unittest.TestCase):
    def test_prices_from_reported_examples_are_recognized(self):
        cases = {
            'R$ 1.981 em 10x s/ juros': ('1.981,00', 'em 10x s/ juros'),
            '✅ R$2245 no PIX': ('2.245,00', 'no PIX'),
            '✅ R$3212 no PIX': ('3.212,00', 'no PIX'),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                info = price_info(text)
                self.assertIsNotNone(info)
                self.assertEqual((info['price'], info['price_condition']), expected)

    def test_unresolved_meli_link_is_enqueued_instead_of_dropped(self):
        handlers = {}
        msg = message()
        chat = SimpleNamespace(username='origem', noforwards=False)
        event = SimpleNamespace(message=msg, chat_id=-123, get_chat=AsyncMock(return_value=chat))
        client = Mock()
        client.start = AsyncMock()
        client.disconnect = AsyncMock()

        async def dialogs():
            yield SimpleNamespace(id=-123, entity=chat, is_group=False, is_channel=True)
        client.iter_dialogs = dialogs

        def on(event_type):
            def register(fn):
                handlers[type(event_type).__name__] = fn
                return fn
            return register
        client.on = on

        async def download(photo, file):
            Path(file).write_bytes(b'image')
            return file
        client.download_media = download

        async def events():
            await handlers['NewMessage'](event)
        client.run_until_disconnected = events

        def blocked(url, report):
            self.assertEqual(url, SHORT)
            report('HTTP 403 em meli.la')
            return None

        with tempfile.TemporaryDirectory() as directory, \
             patch.object(monitor, 'BASE', Path(directory)), \
             patch.object(monitor, 'TelegramClient', return_value=client), \
             patch.object(monitor, 'resolve', side_effect=blocked), \
             patch('sys.argv', ['monitor_ofertas.py']), \
             patch.dict(os.environ, {
                 'TG_API_ID': '123',
                 'TG_API_HASH': 'fake',
                 'TG_CHATS': '-123',
                 'TG_MEDIA_CHATS': '-123',
                 'TELEGRAM_CANAL': '@destino',
                 'ML_MANUAL_CHAT': '',
             }), \
             redirect_stdout(io.StringIO()):
            asyncio.run(monitor.main())

            queue = CapturedOfferQueue(Path(directory) / 'publicacoes.sqlite3')
            rows = queue.pending()
            queue.close()
            offers = [row for row in rows if row.get('kind') == 'ml_offer_pending']
            self.assertEqual(len(offers), 1)
            offer = offers[0]
            self.assertEqual(offer['url'], SHORT)
            self.assertEqual(offer['product_id'], manual.pending_key(SHORT))
            self.assertEqual(offer['price'], '1.981,00')
            self.assertEqual(offer['price_condition'], 'em 10x s/ juros')
            self.assertTrue(offer['image'])

    def test_pending_offer_is_normalized_after_public_read(self):
        entry = {
            'kind': 'ml_offer_pending',
            'source': 'telegram',
            'store': 'Mercado Livre',
            'product_id': manual.pending_key(SHORT),
            'url': SHORT,
            'chat_id': -123,
            'message_id': 321,
            'source_date': datetime.now(timezone.utc).isoformat(),
            'name': 'Ar-Condicionado Gree',
            'price': '1.981,00',
            'price_condition': 'em 10x s/ juros',
            'price_from': False,
            'coupon': 'HOJEVA1',
            'image': None,
        }
        found = auto.extract_product(document(), DIRECT)
        with patch.object(auto, 'fetch_http', return_value=found):
            ready = auto.enrich(entry)
        self.assertEqual(ready['kind'], 'ml_offer')
        self.assertEqual(ready['product_id'], 'MercadoLivre:123456')
        self.assertEqual(ready['url'], CANONICAL)
        self.assertEqual(ready['original_url'], SHORT)
        self.assertEqual(ready['price'], '1.981,00')
        self.assertEqual(ready['price_condition'], 'em 10x s/ juros')
        self.assertEqual(ready['api_image'], IMAGE)

    def test_publisher_sends_pending_offer_to_ml_affiliate_flow(self):
        pending = {
            'kind': 'ml_offer_pending',
            'source': 'telegram',
            'store': 'Mercado Livre',
            'product_id': manual.pending_key(SHORT),
            'url': SHORT,
            'chat_id': -123,
            'message_id': 321,
            'source_date': datetime.now(timezone.utc).isoformat(),
            'name': 'Ar-Condicionado Gree',
            'price': '1.981,00',
            'price_condition': 'em 10x s/ juros',
            'price_from': False,
            'coupon': None,
            'image': 'imagens_ofertas/x.jpg',
        }
        ready = dict(
            pending,
            kind='ml_offer',
            product_id='MercadoLivre:123456',
            url=CANONICAL,
            original_url=SHORT,
            api_image=IMAGE,
        )
        fake_ml = Mock()
        fake_ml.prepare.side_effect = lambda offer: dict(
            offer,
            affiliate_url='https://meli.la/AFILIADO123',
            affiliate_generated=True,
        )
        reader = Mock()
        reader.read.return_value = ready

        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(Path(directory) / 'posts.db')
            with patch.object(publisher, 'Ledger', return_value=ledger), \
                 patch.object(publisher.ShopeeAffiliate, 'from_env', side_effect=AffiliateError('sem Shopee')), \
                 patch.object(publisher.MercadoLivreAffiliate, 'from_env', return_value=fake_ml), \
                 patch.object(publisher, 'rows', return_value=iter([pending])), \
                 patch.object(auto, 'AutoReader', return_value=reader), \
                 patch.dict(os.environ, {
                     'TELEGRAM_TOKEN': 'fake',
                     'TELEGRAM_CANAL': '@fake',
                     'EXIGIR_IMAGEM': '1',
                 }), \
                 redirect_stdout(io.StringIO()):
                publisher.run_publisher(SimpleNamespace(simular=True), Mock())

            reader.read.assert_called_once()
            self.assertTrue(reader.read.call_args.kwargs['blocking'])
            fake_ml.prepare.assert_called_once()
            self.assertEqual(fake_ml.prepare.call_args.args[0]['product_id'], 'MercadoLivre:123456')
            ledger.db.close()


if __name__ == '__main__':
    unittest.main()
