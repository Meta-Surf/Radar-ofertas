"""Métricas de qualidade das fontes Telegram, sem interferir na publicação."""
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from revisao_publicacao import matches

STATUSES = {'RECEBIDA', 'CAPTADA', 'AGUARDANDO', 'REJEITADA', 'PUBLICADA'}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class SourceMetrics:
    """Persistência fail-open por mensagem de origem."""
    def __init__(self, path):
        self.path = Path(path)
        self.enabled = True
        self.start_at = None
        try:
            self.start_at = self._ensure_schema()
        except Exception as error:
            self.enabled = False
            logging.warning('Métricas de fontes indisponíveis: %s', type(error).__name__)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA busy_timeout=5000')
        return db
    def _ensure_schema(self):
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS source_messages (
                    chat_id TEXT NOT NULL,
                    source_message_id INTEGER NOT NULL,
                    source_name TEXT,
                    source_username TEXT,
                    source_date TEXT,
                    store TEXT,
                    kind TEXT,
                    product_id TEXT,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    has_price INTEGER NOT NULL DEFAULT 0,
                    has_image INTEGER NOT NULL DEFAULT 0,
                    captured INTEGER NOT NULL DEFAULT 0,
                    published INTEGER NOT NULL DEFAULT 0,
                    recovered INTEGER NOT NULL DEFAULT 0,
                    first_seen_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    published_at TEXT,
                    published_message_id INTEGER,
                    PRIMARY KEY(chat_id, source_message_id)
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS source_metric_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL,
                    source_message_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE(chat_id, source_message_id, status, reason)
                )
            """)
            db.execute('CREATE TABLE IF NOT EXISTS source_metrics_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('INSERT OR IGNORE INTO source_metrics_meta VALUES (?, ?)', ('start_at', utc_now()))
            return db.execute('SELECT value FROM source_metrics_meta WHERE key=?', ('start_at',)).fetchone()[0]
    def record(self, chat_id, message_id, status, reason='', **fields):
        if not self.enabled or chat_id is None or message_id is None:
            return False
        source_date = fields.get('source_date')
        if source_date and self.start_at:
            try:
                source_time = datetime.fromisoformat(str(source_date)).astimezone(timezone.utc)
                start_time = datetime.fromisoformat(self.start_at).astimezone(timezone.utc)
                if source_time < start_time - timedelta(seconds=5):
                    return False
            except (TypeError, ValueError):
                pass
        status = str(status or '').upper()
        if status not in STATUSES:
            return False
        now = utc_now()
        reason = str(reason or '')[:120]
        values = {
            'source_name': fields.get('source_name'),
            'source_username': fields.get('source_username'),
            'source_date': fields.get('source_date'),
            'store': fields.get('store'),
            'kind': fields.get('kind'),
            'product_id': fields.get('product_id'),
            'has_price': int(bool(fields.get('has_price'))),
            'has_image': int(bool(fields.get('has_image'))),
            'captured': int(bool(fields.get('captured') or status in {'CAPTADA', 'AGUARDANDO', 'PUBLICADA'})),
            'published': int(bool(fields.get('published') or status == 'PUBLICADA')),
            'recovered': int(bool(fields.get('recovered'))),
        }
        try:
            with self._connect() as db:
                selected = fields.get('_queue_selection')
                if selected is not None:
                    db.execute('BEGIN IMMEDIATE')
                    if not matches(db, selected, require_active=False):
                        # PUBLICADA é histórico da confirmação já consumida. Não
                        # atribuir a confirmação antiga à mensagem editada viva.
                        newer = db.execute(
                            'SELECT 1 FROM captured_queue WHERE chat_id=? AND message_id=? LIMIT 1',
                            (str(chat_id), int(message_id))).fetchone()
                        if status != 'PUBLICADA' or newer:
                            return False
                previous = db.execute(
                    'SELECT status, published FROM source_messages WHERE chat_id=? AND source_message_id=?',
                    (str(chat_id), int(message_id))
                ).fetchone()
                final_status = 'PUBLICADA' if previous and previous[1] and status != 'PUBLICADA' else status
                db.execute("""
                    INSERT INTO source_messages (
                        chat_id, source_message_id, source_name, source_username,
                        source_date, store, kind, product_id, status, reason,
                        has_price, has_image, captured, published, recovered,
                        first_seen_at, updated_at, published_at, published_message_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(chat_id, source_message_id) DO UPDATE SET
                        source_name=COALESCE(excluded.source_name, source_messages.source_name),
                        source_username=COALESCE(excluded.source_username, source_messages.source_username),
                        source_date=COALESCE(excluded.source_date, source_messages.source_date),
                        store=COALESCE(excluded.store, source_messages.store),
                        kind=COALESCE(excluded.kind, source_messages.kind),
                        product_id=COALESCE(excluded.product_id, source_messages.product_id),
                        status=excluded.status,
                        reason=excluded.reason,
                        has_price=MAX(source_messages.has_price, excluded.has_price),
                        has_image=MAX(source_messages.has_image, excluded.has_image),
                        captured=MAX(source_messages.captured, excluded.captured),
                        published=MAX(source_messages.published, excluded.published),
                        recovered=MAX(source_messages.recovered, excluded.recovered),
                        updated_at=excluded.updated_at,
                        published_at=COALESCE(excluded.published_at, source_messages.published_at),
                        published_message_id=COALESCE(excluded.published_message_id, source_messages.published_message_id)
                """, (
                    str(chat_id), int(message_id), values['source_name'], values['source_username'],
                    values['source_date'], values['store'], values['kind'], values['product_id'],
                    final_status, '' if final_status == 'PUBLICADA' else reason,
                    values['has_price'], values['has_image'], values['captured'], values['published'],
                    values['recovered'], now, now, now if status == 'PUBLICADA' else None,
                    fields.get('published_message_id')
                ))
                db.execute(
                    'INSERT OR IGNORE INTO source_metric_events '
                    '(chat_id, source_message_id, status, reason, created_at) VALUES (?, ?, ?, ?, ?)',
                    (str(chat_id), int(message_id), status, reason, now)
                )
            return True
        except Exception as error:
            logging.warning('Falha ao registrar métrica de fonte: %s', type(error).__name__)
            return False

    def record_offer(self, offer, status, reason='', published_message_id=None, *, selected=None):
        if not isinstance(offer, dict):
            return False
        return self.record(
            offer.get('chat_id'), offer.get('message_id'), status, reason,
            source_name=offer.get('source_name'),
            source_username=offer.get('source_username'),
            source_date=offer.get('source_date'),
            store=offer.get('store'),
            kind=offer.get('kind'),
            product_id=offer.get('product_id'),
            has_price=bool(offer.get('price')),
            has_image=bool(offer.get('image') or offer.get('api_image')),
            captured=True,
            recovered=bool(offer.get('recovered')),
            published_message_id=published_message_id,
            _queue_selection=selected,
        )
