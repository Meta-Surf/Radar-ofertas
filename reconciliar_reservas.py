"""Reconciliação explícita de envios incertos, sem liberação automática."""
import argparse
import json
import os
import sqlite3
import time
from pathlib import Path

from distribuicao import payload_revision
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
DB = BASE / "publicacoes.sqlite3"
load_dotenv(BASE / ".env", encoding="utf-8-sig")
TELEGRAM_CHANNEL = str(os.getenv("TELEGRAM_CANAL") or "")


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


def selected_reservation(db, product, day):
    """Chamador mantém BEGIN IMMEDIATE até confirmar todas as estruturas."""
    args = [product]
    sql = "SELECT day,status,message_id FROM posts WHERE product=?"
    if day:
        sql += " AND day=?"
        args.append(day)
    rows = db.execute(sql, args).fetchall()
    if len(rows) != 1:
        raise SystemExit(
            f"Nenhuma resolução feita: esperado 1 registro; encontrados {len(rows)}. "
            "Use --dia para desambiguar."
        )
    selected_day, status, message_id = rows[0]
    if status not in {"uncertain", "sent"}:
        raise SystemExit("Nenhuma resolução feita: estado não reconciliável.")
    deliveries = db.execute(
        "SELECT id,state,external_id,payload,destination_account,surface FROM deliveries "
        "WHERE product=? AND day=? AND destination='telegram'",
        (product, selected_day),
    ).fetchall()
    if len(deliveries) > 1 or any(
        row[4] != TELEGRAM_CHANNEL or row[5] != "channel" for row in deliveries
    ):
        raise SystemExit("Nenhuma resolução feita: conta/superfície Telegram ambígua ou incompatível.")
    delivery = deliveries[0] if deliveries else None
    if delivery and delivery[1] not in {"UNCERTAIN", "SENT"}:
        raise SystemExit("Nenhuma resolução feita: estado de entrega não reconciliável.")
    return selected_day, status, message_id, delivery


def resolve_not_sent(db, product, day):
    with db:
        db.execute("BEGIN IMMEDIATE")
        selected_day, status, _, delivery = selected_reservation(db, product, day)
        if status == "sent" or (delivery and delivery[1] == "SENT"):
            raise SystemExit("Nenhuma resolução feita: publicação SENT confirmada não pode ser liberada.")
        result = db.execute(
            "DELETE FROM posts WHERE product=? AND day=? AND status='uncertain'",
            (product, selected_day),
        )
        if result.rowcount != 1:
            raise SystemExit("Nenhuma resolução feita: reserva mudou durante a resolução.")
        if delivery:
            result = db.execute("DELETE FROM deliveries WHERE id=? AND state='UNCERTAIN'", (delivery[0],))
            if result.rowcount != 1:
                raise SystemExit("Nenhuma resolução feita: entrega mudou durante a resolução.")
    print("Liberado como NÃO ENVIADO:", product)


def record_confirmed_history(db, product, message_id, payload, now):
    """Só persiste preço que já estava no payload de envio; não toca filas."""
    from inteligencia_ofertas import cents, comparison_key
    if payload and payload.get("product_id") != product:
        raise SystemExit("Nenhuma resolução feita: payload não corresponde ao produto selecionado.")
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='price_history'").fetchone():
        if payload.get("price"):
            raise SystemExit("Nenhuma resolução feita: histórico de preço indisponível.")
        return
    previous = db.execute(
        "SELECT product,cents FROM price_history WHERE channel=? AND message_id=?",
        (TELEGRAM_CHANNEL, message_id),
    ).fetchone()
    if previous and previous[0] != product:
        raise SystemExit("Nenhuma resolução feita: message_id já pertence a outro produto no histórico.")
    value = cents(payload)
    if previous and value and previous[1] != value:
        raise SystemExit("Nenhuma resolução feita: preço conflitante para message_id no histórico.")
    if value and payload.get("kind") != "coupon_alert":
        db.execute(
            "INSERT OR IGNORE INTO price_history "
            "(product,comparison,cents,published,channel,message_id,payload) VALUES (?,?,?,?,?,?,?)",
            (product, comparison_key(payload), value, now, TELEGRAM_CHANNEL,
             message_id, json.dumps(payload, ensure_ascii=False)),
        )


def resolve_sent(db, product, day, message_id):
    if (isinstance(message_id, bool) or not isinstance(message_id, (int, str))
            or not str(message_id).isdigit()):
        raise SystemExit("--enviado exige --message-id inteiro positivo.")
    try:
        message_id = int(message_id)
    except ValueError:
        raise SystemExit("--enviado exige --message-id inteiro positivo.") from None
    if not 0 < message_id <= 2**63 - 1:
        raise SystemExit("--enviado exige --message-id inteiro positivo.")
    with db:
        db.execute("BEGIN IMMEDIATE")
        selected_day, status, previous_id, delivery = selected_reservation(db, product, day)
        if status == "sent" and previous_id != message_id:
            raise SystemExit("Nenhuma resolução feita: SENT tem message_id conflitante.")
        if delivery and delivery[1] == "SENT" and delivery[2] != str(message_id):
            raise SystemExit("Nenhuma resolução feita: delivery SENT tem external_id conflitante.")
        if status == "sent" and delivery and delivery[1] == "SENT":
            return  # Idempotência: preserva payload, IDs, histórico e timestamps.
        try:
            payload = json.loads(delivery[3]) if delivery else {}
        except (TypeError, ValueError):
            raise SystemExit("Nenhuma resolução feita: payload da entrega inválido.") from None
        if not isinstance(payload, dict):
            raise SystemExit("Nenhuma resolução feita: payload da entrega inválido.")
        now = time.time()
        record_confirmed_history(db, product, message_id, payload, now)
        result = db.execute(
            "UPDATE posts SET status='sent',message_id=?,updated_at=? WHERE product=? AND day=?",
            (message_id, now, product, selected_day),
        )
        if result.rowcount != 1:
            raise SystemExit("Nenhuma resolução feita: reserva mudou durante a resolução.")
        if delivery:
            result = db.execute(
                "UPDATE deliveries SET state='SENT',external_id=?,attempts=MAX(attempts,1),"
                "retry_at=NULL,error_reason='',updated_at=?,sent_at=? WHERE id=?",
                (str(message_id), now, now, delivery[0]),
            )
            if result.rowcount != 1:
                raise SystemExit("Nenhuma resolução feita: entrega mudou durante a resolução.")
        else:
            db.execute(
                "INSERT INTO deliveries "
                "(product,destination,destination_account,surface,day,revision,state,external_id,"
                "attempts,payload,created_at,updated_at,sent_at) "
                "VALUES (?,'telegram',?,'channel',?,?,'SENT',?,1,'{}',?,?,?)",
                (product, TELEGRAM_CHANNEL, selected_day, payload_revision({}),
                 str(message_id), now, now, now),
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
