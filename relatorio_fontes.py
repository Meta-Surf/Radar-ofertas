"""Relatório de qualidade das fontes Telegram."""
import argparse
import collections
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / '.env', encoding='utf-8-sig')

PERIODS = {
    '24h': timedelta(hours=24),
    '7d': timedelta(days=7),
    '30d': timedelta(days=30),
    'total': None,
}


def configured(name):
    return {x.strip().lower() for x in os.getenv(name, '').split(',') if x.strip()}


def source_state(chat_id, username):
    paused = configured('TG_PAUSED_CHATS')
    keys = {str(chat_id).lower()}
    if username:
        keys.add('@' + str(username).lstrip('@').lower())
    return 'PAUSADO' if keys & paused else 'ATIVO'
def load_rows(period):
    db = sqlite3.connect(BASE / 'publicacoes.sqlite3')
    query = """
        SELECT chat_id, source_name, source_username, status, reason,
               captured, published, first_seen_at
        FROM source_messages
    """
    params = ()
    if PERIODS[period] is not None:
        cutoff = datetime.now(timezone.utc) - PERIODS[period]
        query += ' WHERE first_seen_at >= ?'
        params = (cutoff.isoformat(),)
    try:
        rows = db.execute(query, params).fetchall()
    except sqlite3.OperationalError:
        rows = []
    db.close()
    return rows


def aggregate(rows):
    result = {}
    for chat_id, name, username, status, reason, captured, published, _ in rows:
        item = result.setdefault(chat_id, {
            'name': name or username or chat_id,
            'username': username or '',
            'received': 0, 'captured': 0, 'published': 0,
            'rejected': 0, 'waiting': 0, 'reasons': collections.Counter(),
        })
        if name:
            item['name'] = name
        if username:
            item['username'] = username
        item['received'] += 1
        item['captured'] += int(bool(captured))
        item['published'] += int(bool(published))
        item['rejected'] += int(status == 'REJEITADA')
        item['waiting'] += int(status == 'AGUARDANDO')
        if reason and status in {'REJEITADA', 'AGUARDANDO'}:
            item['reasons'][reason] += 1
    return result


def print_report(period, data):
    print(f'QUALIDADE DAS FONTES — {period}')
    if not data:
        print('Nenhuma métrica registrada neste período.')
        return
    header = ('Fonte', 'Estado', 'Receb.', 'Capt.', 'Publ.', 'Rej.', 'Aguard.', 'Aproveit.')
    print(f'{header[0]:34} {header[1]:8} {header[2]:>6} {header[3]:>6} '
          f'{header[4]:>6} {header[5]:>5} {header[6]:>7} {header[7]:>9}')
    print('-' * 94)
    ordered = sorted(data.items(), key=lambda item: (-item[1]['published'], -item[1]['captured'], item[1]['name']))
    for chat_id, item in ordered:
        rate = 100 * item['published'] / item['received'] if item['received'] else 0
        name = item['name'][:34]
        state = source_state(chat_id, item['username'])
        print(f'{name:34} {state:8} {item["received"]:6d} {item["captured"]:6d} '
              f'{item["published"]:6d} {item["rejected"]:5d} {item["waiting"]:7d} {rate:8.1f}%')
    print('\nPrincipais causas de perda/espera:')
    for chat_id, item in ordered:
        if not item['reasons']:
            continue
        reasons = ', '.join(f'{reason}={count}' for reason, count in item['reasons'].most_common(3))
        print(f'- {item["name"]}: {reasons}')


def main():
    parser = argparse.ArgumentParser(description='Relatório de qualidade das fontes Telegram.')
    parser.add_argument('--periodo', choices=PERIODS, default='7d')
    args = parser.parse_args()
    print_report(args.periodo, aggregate(load_rows(args.periodo)))


if __name__ == '__main__':
    main()