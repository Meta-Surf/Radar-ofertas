"""Fila persistente, preferências de marcas e histórico de divulgações confirmadas."""
import hashlib
import json
import math
import re
import time
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path
from functools import lru_cache

DAY = 86400
TTL = 2 * 3600


def normalized(value):
    return re.sub(r'\s+', ' ', ''.join(c for c in unicodedata.normalize('NFKD', str(value))
                  if not unicodedata.combining(c)).lower()).strip()


def cents(offer):
    try:
        value = Decimal(str(offer['price']).replace('.', '').replace(',', '.'))
        return int(value * 100) if value.is_finite() and value > 0 and value * 100 == int(value * 100) else None
    except (KeyError, ValueError, InvalidOperation, OverflowError):
        return None


def comparison_key(offer):
    # Não presumir variante a partir de preço mínimo, título ou preço único.
    variant = offer.get('variant_id')
    if offer.get('price_from') or not variant or not offer.get('variant_verified'):
        return None
    condition = [normalized(offer.get('price_condition', '')),
                 normalized(offer.get('coupon', '')),
                 'api_antes_descontos' if offer.get('source') == 'shopee_api' else 'grupo']
    return hashlib.sha256(json.dumps([offer['product_id'], str(variant), condition],
                                     ensure_ascii=False).encode()).hexdigest()


@lru_cache(maxsize=1)
def brand_config():
    return json.loads((Path(__file__).parent / 'marcas_radar.json').read_text(encoding='utf-8'))


def brand_match(offer):
    theme = offer.get('tema_radar')
    if not theme:
        return False
    from radar_shopee_continuo import pertence_ao_tema
    if not pertence_ao_tema(theme, offer):
        return False
    config = brand_config()
    title = normalized(offer.get('name', ''))
    # Compatibilidade e comparações não identificam o fabricante do produto.
    title = re.split(r'\b(?:compativel|similar|tipo|para|vs)\b', title)[0]
    return any(re.search(r'(?<!\w)' + re.escape(normalized(alias)) + r'(?!\w)', title)
               for alias in config.get(theme, []))


def quality(offer):
    return (40 * float(offer.get('rating', 0)) / 5 +
            35 * min(math.log10(1 + max(0, float(offer.get('sales', 0)))) / 4, 1) +
            25 * min(float(offer.get('discount', 0)), 60) / 60)


class Intelligence:
    def __init__(self, db):
        self.db = db
        db.executescript('''
        CREATE TABLE IF NOT EXISTS radar_queue (
          product TEXT PRIMARY KEY, payload TEXT NOT NULL, refreshed REAL NOT NULL,
          expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS price_history (
          id INTEGER PRIMARY KEY, product TEXT NOT NULL, comparison TEXT,
          cents INTEGER NOT NULL, published REAL NOT NULL, channel TEXT NOT NULL,
          message_id INTEGER NOT NULL, payload TEXT NOT NULL,
          UNIQUE(channel, message_id));
        CREATE INDEX IF NOT EXISTS price_comparison ON price_history(channel, comparison, published);
        CREATE TABLE IF NOT EXISTS radar_rotation (theme TEXT PRIMARY KEY, published REAL NOT NULL);
        ''')

    def enqueue(self, offers, now=None):
        now = time.time() if now is None else now
        with self.db:
            self.db.execute('DELETE FROM radar_queue WHERE expires<=?', (now,))
            for offer in offers:
                if offer.get('source') != 'shopee_api' or not cents(offer):
                    continue
                self.db.execute('''INSERT INTO radar_queue VALUES (?,?,?,?)
                  ON CONFLICT(product) DO UPDATE SET payload=excluded.payload,
                  refreshed=excluded.refreshed, expires=excluded.expires''',
                  (offer['product_id'], json.dumps(offer, ensure_ascii=False), now, now + TTL))

    def discard(self, product):
        with self.db:
            self.db.execute('DELETE FROM radar_queue WHERE product=?', (product,))

    def badge(self, offer, channel, now=None):
        now = time.time() if now is None else now
        key, value = comparison_key(offer), cents(offer)
        if not key or not value:
            return ''
        first = self.db.execute('SELECT MIN(published) FROM price_history WHERE comparison=? AND channel=?',
                                (key, channel)).fetchone()[0]
        if first is None:
            return ''
        for days in (180, 90, 60, 30):
            if first > now - days * DAY:
                continue
            minimum = self.db.execute('''SELECT MIN(cents) FROM price_history
                 WHERE comparison=? AND channel=? AND published>=? AND published<?''',
                 (key, channel, now - days * DAY, now)).fetchone()[0]
            if minimum is not None and value <= minimum:
                verb = 'Novo menor preço divulgado' if value < minimum else 'Iguala o menor preço divulgado'
                return f'📉 {verb} neste canal nos últimos {days} dias.'
        return ''

    def can_repeat(self, offer, channel, now=None):
        now = time.time() if now is None else now
        key, value = comparison_key(offer), cents(offer)
        if not key or not value:
            return False
        last = self.db.execute('''SELECT cents, published, comparison FROM price_history
             WHERE product=? AND channel=? ORDER BY published DESC LIMIT 1''',
             (offer['product_id'], channel)).fetchone()
        return bool(last and last[2] == key and now - last[1] >= DAY and value < last[0])

    def record(self, offer, channel, message_id, now=None):
        now = time.time() if now is None else now
        value = cents(offer)
        if not value:
            return
        # Transação do chamador: a confirmação e o histórico são atômicos.
        self.db.execute('INSERT OR IGNORE INTO price_history '
                        '(product,comparison,cents,published,channel,message_id,payload) VALUES (?,?,?,?,?,?,?)',
                        (offer['product_id'], comparison_key(offer), value, now, channel,
                         message_id, json.dumps(offer, ensure_ascii=False)))
        self.db.execute('DELETE FROM radar_queue WHERE product=?', (offer['product_id'],))
        if offer.get('source') == 'shopee_api' and offer.get('tema_radar'):
            self.db.execute('INSERT OR REPLACE INTO radar_rotation VALUES (?,?)',
                            (offer['tema_radar'], now))

    def pending(self, channel='', now=None):
        now = time.time() if now is None else now
        with self.db:
            self.db.execute('DELETE FROM radar_queue WHERE expires<=?', (now,))
        offers = []
        for product, payload in self.db.execute('SELECT product,payload FROM radar_queue'):
            offer = json.loads(payload)
            states = self.db.execute('SELECT status FROM posts WHERE product=?', (product,)).fetchall()
            if states and (any(s[0] != 'sent' for s in states) or not self.can_repeat(offer, channel, now)):
                continue
            offers.append(offer)
        rotation = dict(self.db.execute('SELECT theme,published FROM radar_rotation'))
        # Marca, categoria premium e histórico influenciam o ranking.
        # Loja oficial só conta quando houver sinal booleano explícito da origem.
        def rank(o):
            return (quality(o) + 12 * brand_match(o)
                    + 18 * bool(o.get('radar_premium'))
                    + 10 * (o.get('official_store') is True)
                    + 15 * bool(self.badge(o, channel, now)))
        ranks = {o['product_id']: rank(o) for o in offers}
        rank = lambda o: ranks[o['product_id']]
        ordered = []
        while offers:
            top = max(rank(o) for o in offers)
            comparable = [o for o in offers if rank(o) >= top - 5]
            chosen = min(comparable, key=lambda o: (rotation.get(o.get('tema_radar'), 0), -rank(o), o['product_id']))
            ordered.append(chosen)
            rotation[chosen.get('tema_radar')] = now + len(ordered)
            offers.remove(chosen)
        return ordered
