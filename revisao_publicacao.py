"""Identidade de consumo e fronteira de envio; nenhum lock durante rede."""
import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def selection(table, key, payload):
    if table not in ('captured_queue', 'radar_queue'):
        raise ValueError('Fila de publicação inválida')
    return {'table': table, 'key': str(key), 'digest': digest(payload)}


def selection_id(selected):
    return json.dumps(selected, sort_keys=True, separators=(',', ':'))


def retry_key(selected):
    return selected['table'] + ':' + selected['key']


@contextmanager
def immediate(db):
    """Participa da transação existente; só confirma a transação que abriu."""
    owned = not db.in_transaction
    if owned:
        db.execute('BEGIN IMMEDIATE')
    try:
        yield
        if owned:
            db.commit()
    except BaseException:
        if owned:
            db.rollback()
        raise


def current_row(db, selected):
    if not isinstance(selected, dict):
        return None
    table = selected.get('table')
    if table == 'captured_queue':
        sql = 'SELECT payload,expires_at FROM captured_queue WHERE queue_key=?'
    elif table == 'radar_queue':
        sql = 'SELECT payload,expires FROM radar_queue WHERE product=?'
    else:
        return None
    return db.execute(sql, (selected.get('key'),)).fetchone()


def matches(db, selected, *, now=None, require_active=True):
    row = current_row(db, selected)
    return bool(row and digest(row[0]) == selected.get('digest')
                and (not require_active or row[1] > (time.time() if now is None else now)))


def discard_selected(db, selected):
    """Compare/delete sob o mesmo lock; pode confirmar uma revisão já expirada."""
    with immediate(db):
        if not matches(db, selected, require_active=False):
            return 0
        if selected['table'] == 'captured_queue':
            sql = 'DELETE FROM captured_queue WHERE queue_key=?'
        else:
            sql = 'DELETE FROM radar_queue WHERE product=?'
        return db.execute(sql, (selected['key'],)).rowcount


def catalog_revision(db, product_id):
    cursor = db.execute('SELECT * FROM kabum_products WHERE product_id=?', (str(product_id),))
    row = cursor.fetchone()
    return catalog_digest(dict(zip([c[0] for c in cursor.description], row))) if row else None


def catalog_digest(row):
    return digest(json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(',', ':')))


@contextmanager
def catalog_guard(path, proof):
    """Só bloqueia escrita da fonte; a única confirmação é no banco do ledger.

    Ordem obrigatória: catálogo primeiro, ledger depois. O coletor grava e
    libera catálogo antes de enqueue. Não há transação distribuída nem escrita
    no catálogo aqui; crash libera seu lock, preservando o commit do ledger.
    """
    if proof is None:
        yield True
        return
    db = None
    try:
        db = sqlite3.connect('file:' + str(path) + '?mode=rw', uri=True, timeout=5)
        db.execute('BEGIN IMMEDIATE')
        yield (catalog_revision(db, proof['product_id']) == proof['digest']
               and proof.get('expires', float('inf')) > time.time())
    finally:
        if db is not None:
            db.rollback()
            db.close()
