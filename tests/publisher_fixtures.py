"""Fixtures do consumidor incluem linhas reais no SQLite temporário."""
import json
from pathlib import Path
from fila_ofertas_sqlite import CapturedOfferQueue, _queue_key
from revisao_publicacao import selection


def persisted_rows(offers, base):
    queue = CapturedOfferQueue(Path(base) / 'publicacoes.sqlite3')
    try:
        queue.replace_capture(offers)
        result = []
        for offer in offers:
            key = _queue_key(offer)
            row = queue.db.execute('SELECT payload FROM captured_queue WHERE queue_key=?', (key,)).fetchone()
            if row:
                current = json.loads(row[0])
                current['_queue_selection'] = selection('captured_queue', key, row[0])
                result.append(current)
        return iter(result)
    finally:
        queue.close()
