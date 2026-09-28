"""Consulta local, somente leitura. Não chama APIs nem envia mensagens."""
import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--produto', help='Exemplo: Shopee:123:456')
    args = parser.parse_args()
    path = Path(__file__).resolve().parent / 'publicacoes.sqlite3'
    if not path.exists():
        parser.exit(0, 'Banco ainda não encontrado nesta pasta.\n')
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='price_history'").fetchone():
            parser.exit(0, 'O histórico de preços ainda não foi iniciado.\n')
        query = 'SELECT product,cents,published,channel,message_id,comparison FROM price_history'
        rows = db.execute(query + (' WHERE product=?' if args.produto else '') +
                          ' ORDER BY published DESC LIMIT 100', (args.produto,) if args.produto else ()).fetchall()
        for product, cents, when, channel, message, comparison in rows:
            stamp = datetime.fromtimestamp(when, ZoneInfo('America/Sao_Paulo')).isoformat()
            print(f'{stamp} | {product} | R$ {cents/100:.2f} | {channel} | mensagem {message} | comparação: {"habilitada" if comparison else "variante não confirmada"}')
        print(f'{len(rows)} registros exibidos (até 100 mais recentes).')
    finally:
        db.close()

if __name__ == '__main__':
    main()
