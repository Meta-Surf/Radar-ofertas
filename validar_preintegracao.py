"""Teste de carga/falha local para a fundação multicanal.

Usa somente banco e arquivos temporários. Não chama Telegram, Meta ou lojas.
"""
import argparse
import json
import sqlite3
import tempfile
import time
from pathlib import Path

from distribuicao import DeliveryOutbox
from multicanal_shadow import ShadowDistribution


def rate(count, started):
    elapsed = max(time.perf_counter() - started, 0.000001)
    return round(count / elapsed, 1), round(elapsed * 1000, 1)


def run(items=5000):
    items = max(100, min(int(items), 50000))
    results = {"items": items, "checks": {}}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        db = sqlite3.connect(root / "load.sqlite3", timeout=30)
        outbox = DeliveryOutbox(db)

        started = time.perf_counter()
        for index in range(items):
            offer = {
                "product_id": f"Load:{index}",
                "name": "Produto de carga",
                "price": f"{100 + (index % 50)},90",
            }
            outbox.enqueue(
                offer["product_id"], destination="instagram", account="dryrun",
                surface="feed", day="2026-10-02", payload=offer, commit=False,
            )
        db.commit()
        results["outbox_enqueue_per_sec"], results["outbox_enqueue_ms"] = rate(
            items, started
        )
        results["checks"]["outbox_count"] = (
            db.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == items
        )

        ready = outbox.ready("instagram", limit=500)
        started = time.perf_counter()
        for row in ready:
            outbox.claim(row[0], commit=False)
        db.commit()
        results["outbox_claim_per_sec"], results["outbox_claim_ms"] = rate(
            len(ready), started
        )
        results["checks"]["claimed_once"] = (
            db.execute("SELECT COUNT(*) FROM deliveries WHERE state='SENDING'").fetchone()[0]
            == len(ready)
        )
        results["checks"]["double_claim_blocked"] = not outbox.claim(
            ready[0][0] if ready else 0
        )
        outbox.enqueue(
            "Load:crash", destination="instagram", account="dryrun",
            surface="feed", day="2026-10-02", payload={"product_id": "Load:crash"},
        )
        crash_id = db.execute(
            "SELECT id FROM deliveries WHERE product='Load:crash'"
        ).fetchone()[0]
        outbox.claim(crash_id)
        db.execute(
            "UPDATE deliveries SET sending_at=? WHERE id=?",
            (time.time() - 3600, crash_id),
        )
        db.commit()
        moved = outbox.reconcile_stale_sending(stale_seconds=600)
        crash_state = db.execute(
            "SELECT state FROM deliveries WHERE id=?", (crash_id,)
        ).fetchone()[0]
        results["checks"]["crash_becomes_uncertain"] = (
            moved >= 1 and crash_state == "UNCERTAIN"
        )

        image = root / "offer.jpg"
        image.write_bytes(b"x" * 1024)
        shadow = ShadowDistribution(db)
        shadow_items = min(items, 2000)
        started = time.perf_counter()
        for index in range(shadow_items):
            offer = {
                "product_id": f"Shadow:{index}",
                "store": "Shopee",
                "kind": "product_offer",
                "name": f"Produto {index}",
                "price": "99,90",
                "affiliate_url": "https://s.shopee.com.br/telegram-only",
                "affiliate_generated": True,
            }
            shadow.record(
                offer, destination="instagram", day="2026-10-02",
                source_external_id=str(index), image=image, commit=False,
            )
        db.commit()
        results["shadow_prepare_per_sec"], results["shadow_prepare_ms"] = rate(
            shadow_items, started
        )
        counts = shadow.counts()
        results["checks"]["shadow_ready"] = (
            counts.get(("instagram", "READY"), 0) == shadow_items
        )
        sample = json.loads(
            db.execute(
                "SELECT payload FROM delivery_shadow ORDER BY id LIMIT 1"
            ).fetchone()[0]
        )
        results["checks"]["shadow_publish_disabled"] = (
            sample.get("publish_enabled") is False
        )
        source_json = json.dumps(sample.get("source_offer", {}), ensure_ascii=False)
        results["checks"]["telegram_affiliate_not_reused"] = (
            "affiliate_url" not in sample.get("source_offer", {})
            and "https://" not in source_json
            and "s.shopee.com.br" not in sample.get("caption", "")
        )
        db.close()

    results["ok"] = all(results["checks"].values())
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--items", type=int, default=5000)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = run(args.items)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("PRÉ-INTEGRAÇÃO — CARGA E FALHAS")
        for name, value in result.items():
            if name == "checks":
                continue
            print(f"{name}: {value}")
        for name, value in result["checks"].items():
            print(f"{name}: {'OK' if value else 'FALHOU'}")
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
