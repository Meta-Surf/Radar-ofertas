import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from metricas_fontes import SourceMetrics
from relatorio_fontes import aggregate


class SourceMetricsTests(unittest.TestCase):
    def test_status_transitions_are_idempotent_and_publication_is_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'publicacoes.sqlite3'
            metrics = SourceMetrics(path)
            metrics.record(-123, 7, 'RECEBIDA', source_name='Fonte A')
            metrics.record(-123, 7, 'CAPTADA', store='Shopee',
                           product_id='Shopee:1:2', has_price=True)
            metrics.record(-123, 7, 'PUBLICADA', published_message_id=99)
            metrics.record(-123, 7, 'RECEBIDA')
            db = sqlite3.connect(path)
            row = db.execute(
                'SELECT status,captured,published,published_message_id '
                'FROM source_messages WHERE chat_id=? AND source_message_id=?',
                ('-123', 7)
            ).fetchone()
            events = db.execute('SELECT COUNT(*) FROM source_metric_events').fetchone()[0]
            db.close()
            self.assertEqual(row, ('PUBLICADA', 1, 1, 99))
            self.assertEqual(events, 3)
    def test_fail_open_does_not_raise(self):
        with tempfile.TemporaryDirectory() as directory:
            metrics = SourceMetrics(Path(directory))
            self.assertFalse(metrics.enabled)
            self.assertFalse(metrics.record(-1, 1, 'RECEBIDA'))

    def test_report_aggregates_unique_source_messages(self):
        rows = [
            ('-1', 'Fonte', 'fonte', 'PUBLICADA', '', 1, 1, 'x'),
            ('-1', 'Fonte', 'fonte', 'REJEITADA', 'SEM_PRODUTO', 0, 0, 'x'),
            ('-1', 'Fonte', 'fonte', 'AGUARDANDO', 'PRECO_AUSENTE', 1, 0, 'x'),
        ]
        data = aggregate(rows)['-1']
        self.assertEqual(data['received'], 3)
        self.assertEqual(data['captured'], 2)
        self.assertEqual(data['published'], 1)
        self.assertEqual(data['rejected'], 1)
        self.assertEqual(data['waiting'], 1)
        self.assertEqual(data['reasons']['SEM_PRODUTO'], 1)


class PublisherMetricsTests(unittest.TestCase):
    def test_successful_telegram_offer_becomes_published(self):
        import bot_ofertas_revisao as publisher
        from shopee_afiliados import AffiliateError

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            offer = {
                'product_id': 'Shopee:123:456',
                'url': 'https://shopee.com.br/product/123/456',
                'source': 'telegram', 'store': 'Shopee', 'kind': 'product_offer',
                'name': 'Produto', 'price': '99,90',
                'source_date': datetime.now(timezone.utc).isoformat(),
                'chat_id': -123, 'message_id': 77,
                'source_name': 'Fonte A', 'source_username': 'fontea',
            }
            (base / 'fila_ofertas_v2.jsonl').write_text(
                __import__('json').dumps(offer) + '\n', encoding='utf-8'
            )
            affiliate = Mock()
            affiliate.prepare.side_effect = lambda row: dict(
                row, affiliate_generated=True,
                affiliate_url='https://s.shopee.com.br/teste'
            )
            ml_affiliate = Mock()
            reader = Mock()
            reader.close = Mock()

            env = {
                'TELEGRAM_TOKEN': 'fake', 'TELEGRAM_CANAL': '@teste',
                'EXIGIR_IMAGEM': '0', 'IDADE_MAXIMA_MINUTOS': '120',
            }
            with patch.object(publisher, 'BASE', base),                  patch.dict('os.environ', env),                  patch.object(publisher.ShopeeAffiliate, 'from_env', return_value=affiliate),                  patch.object(publisher.MercadoLivreAffiliate, 'from_env', return_value=ml_affiliate),                  patch('mercadolivre_auto.AutoReader', return_value=reader),                  patch.object(publisher, 'send', return_value=(321, 0)),                  patch.object(publisher.time, 'sleep', side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    publisher.run_publisher(SimpleNamespace(simular=False), Mock())

            db = sqlite3.connect(base / 'publicacoes.sqlite3')
            row = db.execute(
                'SELECT status,published,published_message_id '
                'FROM source_messages WHERE chat_id=? AND source_message_id=?',
                ('-123', 77)
            ).fetchone()
            db.close()
            self.assertEqual(row, ('PUBLICADA', 1, 321))


if __name__ == '__main__':
    unittest.main()