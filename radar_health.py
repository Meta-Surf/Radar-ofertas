"""Diagnóstico operacional do Radar; opcionalmente alerta um chat privado."""
import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
SERVICES = (
    "radar-monitor.service",
    "radar-publicador.service",
    "radar-shopee.service",
)


def systemd_status(name):
    try:
        result = subprocess.run(
            ["systemctl", "is-active", name],
            capture_output=True, text=True, timeout=5, check=False,
        )
        return (result.stdout or result.stderr).strip() or "unknown"
    except Exception:
        return "unknown"


def newest_backup():
    files = sorted(
        Path("/var/backups/radar").glob("radar_backup_*.tar.gz"),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    if not files:
        return None
    path = files[0]
    return {
        "name": path.name,
        "age_hours": round((time.time() - path.stat().st_mtime) / 3600, 1),
        "size_mb": round(path.stat().st_size / 1024 / 1024, 1),
    }


def scalar(db, sql, args=(), default=0):
    try:
        row = db.execute(sql, args).fetchone()
        return row[0] if row and row[0] is not None else default
    except sqlite3.Error:
        return default
def database_health():
    path = BASE / "publicacoes.sqlite3"
    result = {
        "queue_total": 0,
        "queue_sources": {},
        "gate_24h": {},
        "ml_failures": {},
        "ml_quarantined": 0,
        "ml_circuit_seconds": 0,
        "reservation_states": {},
        "last_publication": "",
    }
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    try:
        result["queue_total"] = scalar(db, "SELECT COUNT(*) FROM radar_queue")
        for payload, in db.execute("SELECT payload FROM radar_queue"):
            try:
                offer = json.loads(payload)
            except Exception:
                continue
            source = str(offer.get("source") or "unknown")
            result["queue_sources"][source] = result["queue_sources"].get(source, 0) + 1

        cutoff = time.time() - 86400
        try:
            result["gate_24h"] = dict(db.execute(
                """SELECT reason,COUNT(*) FROM prepublication_gate
                   WHERE checked>=? GROUP BY reason ORDER BY COUNT(*) DESC""",
                (cutoff,),
            ))
        except sqlite3.Error:
            pass

        try:
            result["ml_failures"] = dict(db.execute(
                "SELECT reason,COUNT(*) FROM ml_resolution_failures GROUP BY reason"
            ))
            result["ml_quarantined"] = scalar(
                db,
                "SELECT COUNT(*) FROM ml_resolution_failures WHERE quarantine_until>?",
                (time.time(),),
            )
            open_until = scalar(
                db,
                "SELECT open_until FROM ml_resolution_circuit WHERE id=1",
                default=0,
            )
            result["ml_circuit_seconds"] = max(0, round(float(open_until) - time.time()))
        except sqlite3.Error:
            pass

        try:
            result["reservation_states"] = dict(
                db.execute(
                    """SELECT status,COUNT(*) FROM posts
                       WHERE status IN ('reserved','sending','uncertain')
                       GROUP BY status"""
                )
            )
        except sqlite3.Error:
            pass

        published = scalar(
            db,
            "SELECT MAX(published_at) FROM source_messages WHERE published=1",
            default="",
        )
        result["last_publication"] = str(published or "")
    finally:
        db.close()
    return result


def kabum_stock_health():
    path = BASE / "kabum_historico.sqlite3"
    if not path.exists():
        return {"products": 0, "known_stock": 0, "coverage_pct": 0.0}
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    try:
        total = scalar(db, "SELECT COUNT(*) FROM kabum_products")
        known = scalar(
            db,
            """SELECT COUNT(*) FROM kabum_products
               WHERE TRIM(COALESCE(in_stock,''))<>''""",
        )
    finally:
        db.close()
    return {
        "products": int(total),
        "known_stock": int(known),
        "coverage_pct": round((known * 100 / total), 1) if total else 0.0,
    }
def configured(env, *keys):
    return all(str(env.get(key) or "").strip() for key in keys)


def collect():
    env = dict(os.environ)
    services = {name: systemd_status(name) for name in SERVICES}
    db = database_health()
    backup = newest_backup()
    kabum = kabum_stock_health()
    integrations = {
        "shopee": configured(env, "SHOPEE_APP_ID", "SHOPEE_SECRET"),
        "mercadolivre": configured(
            env, "ML_AFFILIATE_COOKIE", "ML_AFFILIATE_CSRF", "ML_AFFILIATE_TAG"
        ),
        "awin_kabum": configured(env, "AWIN_PUBLISHER_ID", "AWIN_ACCESS_TOKEN"),
        "amazon_creators": configured(
            env,
            "AMAZON_PARTNER_TAG",
            "AMAZON_CREATORS_CREDENTIAL_ID",
            "AMAZON_CREATORS_CREDENTIAL_SECRET",
        ),
    }

    critical, warnings = [], []
    for service, state in services.items():
        if state != "active":
            critical.append(f"{service}: {state}")

    if not backup:
        critical.append("backup geral inexistente")
    elif backup["age_hours"] > 48:
        critical.append(f"backup geral antigo: {backup['age_hours']}h")

    uncertain = int(db["reservation_states"].get("uncertain", 0))
    if uncertain:
        warnings.append(f"reservas com envio incerto: {uncertain}")
    if db["ml_circuit_seconds"] > 0:
        warnings.append(
            f"ML circuit breaker aberto por ~{db['ml_circuit_seconds']}s"
        )
    if db["ml_quarantined"]:
        warnings.append(f"ML em quarentena: {db['ml_quarantined']} links")
    if kabum["products"] and kabum["coverage_pct"] == 0:
        warnings.append("KaBuM: estoque oficial sem cobertura")
    if not integrations["amazon_creators"]:
        warnings.append("Amazon: Creators API aguardando credenciais")

    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "services": services,
        "database": db,
        "backup": backup,
        "kabum_stock": kabum,
        "integrations": integrations,
        "critical": critical,
        "warnings": warnings,
    }
def render(report):
    lines = ["RADAR DE OFERTAS — SAÚDE OPERACIONAL"]
    lines.append(
        "Serviços: " + ", ".join(
            f"{name.removeprefix('radar-').removesuffix('.service')}={state}"
            for name, state in report["services"].items()
        )
    )
    db = report["database"]
    lines.append(
        f"Fila radar: {db['queue_total']} | fontes: "
        + ", ".join(f"{k}={v}" for k, v in sorted(db["queue_sources"].items()))
    )
    reservation_states = db.get("reservation_states", {})
    lines.append(
        "Reservas: "
        + ", ".join(
            f"{name}={count}"
            for name, count in sorted(reservation_states.items())
        )
        if reservation_states else "Reservas: nenhuma pendente"
    )
    lines.append(
        f"ML: quarentena={db['ml_quarantined']} | "
        f"circuit={db['ml_circuit_seconds']}s | falhas={db['ml_failures']}"
    )
    stock = report["kabum_stock"]
    lines.append(
        f"KaBuM estoque: {stock['known_stock']}/{stock['products']} "
        f"({stock['coverage_pct']}%)"
    )
    integrations = report["integrations"]
    lines.append(
        "Integrações: " + ", ".join(
            f"{name}={'OK' if value else 'PENDENTE'}"
            for name, value in integrations.items()
        )
    )
    if report["backup"]:
        lines.append(
            f"Backup: {report['backup']['name']} | "
            f"{report['backup']['age_hours']}h | {report['backup']['size_mb']} MB"
        )
    if report["critical"]:
        lines.append("CRÍTICO: " + " | ".join(report["critical"]))
    if report["warnings"]:
        lines.append("AVISOS: " + " | ".join(report["warnings"]))
    if not report["critical"] and not report["warnings"]:
        lines.append("Estado: OK")
    return "\n".join(lines)


def alert(report):
    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_ADMIN_CHAT", "").strip()
    if not token or not chat:
        return False, "TELEGRAM_ADMIN_CHAT não configurado"
    state_path = Path(
        os.getenv(
            "RADAR_HEALTH_STATE",
            str(Path.home() / ".local/state/radar/health.json"),
        )
    )
    fingerprint_data = {
        "critical": report["critical"],
        "warnings": report["warnings"],
        "services": report["services"],
    }
    digest = hashlib.sha256(
        json.dumps(fingerprint_data, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    previous = ""
    try:
        previous = json.loads(state_path.read_text(encoding="utf-8")).get("digest", "")
    except Exception:
        pass
    if digest == previous:
        return False, "sem mudança"
    text = render(report)
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat, "text": text[:3900]},
            timeout=(10, 30),
        )
        data = response.json()
    except Exception:
        return False, "falha de conexão"
    if not data.get("ok"):
        return False, "Telegram recusou o alerta"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"digest": digest, "updated": time.time()}),
        encoding="utf-8",
    )
    return True, "enviado"


def main():
    load_dotenv(BASE / ".env", encoding="utf-8-sig")
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--alert", action="store_true")
    args = parser.parse_args()
    report = collect()
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render(report))
    if args.alert:
        sent, reason = alert(report)
        print("Alerta privado:", reason)
    raise SystemExit(2 if report["critical"] else 0)


if __name__ == "__main__":
    main()
