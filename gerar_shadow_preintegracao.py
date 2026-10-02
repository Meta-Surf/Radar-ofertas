"""Gera amostras shadow a partir de entregas Telegram já confirmadas.

Não executa publicação externa nem chamadas de rede.
"""
import argparse
import json
import sqlite3
from pathlib import Path

from dotenv import load_dotenv
from configuracao import DistributionConfig
from multicanal_shadow import ShadowDistribution

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / '.env', encoding='utf-8-sig')


def media_for(offer):
    if offer.get("kind") == "coupon_alert":
        store = str(offer.get("store") or "")
        try:
            if store == "Mercado Livre":
                from cupons_mercadolivre import banner_path
                return banner_path(BASE)
            if store == "KaBuM":
                from cupons_kabum import banner_path
                return banner_path(BASE)
            if store == "Shopee":
                from cupons_shopee import banner_path
                return banner_path(BASE)
        except Exception:
            pass
    remote = str(offer.get("api_image") or "").strip()
    if remote.startswith("https://"):
        return remote
    local = str(offer.get("image") or "").strip()
    if local:
        path = (BASE / local).resolve()
        if path.is_relative_to(BASE / "imagens_ofertas") and path.is_file():
            return path
    return None


def run(destination="instagram", limit=50):
    config = DistributionConfig.from_env()
    if destination not in config.shadow_destinations:
        raise SystemExit(
            f"{destination} não está em RADAR_DESTINOS_SHADOW; nada foi alterado."
        )
    db = sqlite3.connect(BASE / "publicacoes.sqlite3", timeout=30)
    shadow = ShadowDistribution(db)
    rows = db.execute(
        """SELECT product,day,external_id,payload FROM deliveries
           WHERE destination='telegram' AND state='SENT'
             AND payload IS NOT NULL AND LENGTH(payload)>2
           ORDER BY sent_at DESC,id DESC LIMIT ?""",
        (max(1, min(int(limit), 500)),),
    ).fetchall()
    ready = blocked = invalid = 0
    for product, day, external_id, raw in rows:
        try:
            offer = json.loads(raw)
            if not isinstance(offer, dict) or not offer.get("product_id"):
                invalid += 1
                continue
            result = shadow.record(
                offer, destination=destination, day=day,
                source_external_id=external_id, image=media_for(offer),
            )
            if result["state"] == "READY":
                ready += 1
            else:
                blocked += 1
        except Exception:
            invalid += 1
    counts = shadow.counts()
    db.close()
    return {
        "examined": len(rows),
        "ready": ready,
        "blocked": blocked,
        "invalid": invalid,
        "counts": {f"{k[0]}.{k[1]}": v for k, v in counts.items()},
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", default="instagram")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    result = run(args.destination, args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
