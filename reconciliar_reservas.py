"""Reconciliação explícita de envios incertos, sem liberação automática."""
import argparse
import sqlite3
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB = BASE / "publicacoes.sqlite3"


def uncertain_rows(db):
    return db.execute(
        """SELECT product,day,message_id,reserved_at,send_started_at,updated_at
           FROM posts WHERE status='uncertain'
           ORDER BY COALESCE(updated_at,0), day, product"""
    ).fetchall()


def show(db):
    rows = uncertain_rows(db)
    print("Reservas uncertain:", len(rows))
    for product, day, message_id, reserved_at, send_started_at, updated_at in rows:
        print(
            day, "|", product,
            "| send_started_at=", send_started_at or "-",
            "| updated_at=", updated_at or "-",
        )
    return len(rows)


def resolve_not_sent(db, product, day):
    args = [product]
    sql = "DELETE FROM posts WHERE product=? AND status='uncertain'"
    if day:
        sql += " AND day=?"
        args.append(day)
    with db:
        result = db.execute(sql, args)
    if result.rowcount != 1:
        raise SystemExit(
            f"Nenhuma resolução feita: esperado 1 registro uncertain; encontrados {result.rowcount}."
        )
    print("Liberado como NÃO ENVIADO:", product)


def resolve_sent(db, product, day, message_id):
    args = [int(message_id), time.time(), product]
    sql = """UPDATE posts SET status='sent', message_id=?, updated_at=?
             WHERE product=? AND status='uncertain'"""
    if day:
        sql += " AND day=?"
        args.append(day)
    with db:
        result = db.execute(sql, args)
    if result.rowcount != 1:
        raise SystemExit(
            f"Nenhuma resolução feita: esperado 1 registro uncertain; encontrados {result.rowcount}."
        )
    print("Marcado como ENVIADO:", product, "| mensagem", message_id)


def main():
    parser = argparse.ArgumentParser(
        description="Lista ou resolve manualmente reservas com envio incerto."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--nao-enviado", metavar="PRODUCT_ID")
    group.add_argument("--enviado", metavar="PRODUCT_ID")
    parser.add_argument("--dia", help="YYYY-MM-DD; opcional para desambiguar")
    parser.add_argument("--message-id", type=int)
    args = parser.parse_args()

    db = sqlite3.connect(DB, timeout=30)
    try:
        if not args.nao_enviado and not args.enviado:
            show(db)
            return
        if args.nao_enviado:
            resolve_not_sent(db, args.nao_enviado, args.dia)
            return
        if not args.message_id or args.message_id <= 0:
            parser.error("--enviado exige --message-id positivo.")
        resolve_sent(db, args.enviado, args.dia, args.message_id)
    finally:
        db.close()


if __name__ == "__main__":
    main()
