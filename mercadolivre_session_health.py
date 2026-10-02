"""Saúde persistente da sessão de afiliados Mercado Livre."""
import hashlib
import os
import time

import requests


def credential_fingerprint(cookie, csrf, tag):
    raw = "\0".join((str(cookie or ""), str(csrf or ""), str(tag or ""))).encode()
    return hashlib.sha256(raw).hexdigest()


def send_admin_alert(message, *, transport=None):
    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_ADMIN_CHAT", "").strip()
    if not token or not chat:
        return False
    try:
        response = (transport or requests).post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat, "text": str(message)[:3900]},
            timeout=(10, 30),
        )
        data = response.json()
        return bool(isinstance(data, dict) and data.get("ok"))
    except Exception:
        return False


class MLAffiliateSessionHealth:
    def __init__(self, db, *, now=time.time, notifier=send_admin_alert):
        self.db = db
        self.now = now
        self.notifier = notifier
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS ml_affiliate_session_health (
               id INTEGER PRIMARY KEY CHECK(id=1),
               fingerprint TEXT NOT NULL DEFAULT '',
               state TEXT NOT NULL DEFAULT 'unknown',
               failures INTEGER NOT NULL DEFAULT 0,
               blocked_until REAL NOT NULL DEFAULT 0,
               last_status INTEGER NOT NULL DEFAULT 0,
               last_error TEXT NOT NULL DEFAULT '',
               last_success REAL NOT NULL DEFAULT 0,
               last_failure REAL NOT NULL DEFAULT 0,
               alert_sent INTEGER NOT NULL DEFAULT 0,
               updated_at REAL NOT NULL DEFAULT 0)"""
        )
        self.db.execute(
            "INSERT OR IGNORE INTO ml_affiliate_session_health(id) VALUES (1)"
        )
        self.db.commit()


    def _row(self):
        return self.db.execute(
            """SELECT fingerprint,state,failures,blocked_until,last_status,
                      last_error,last_success,last_failure,alert_sent,updated_at
               FROM ml_affiliate_session_health WHERE id=1"""
        ).fetchone()

    def can_try(self, fingerprint):
        row = self._row()
        now = self.now()
        if row[0] != fingerprint:
            with self.db:
                self.db.execute(
                    """UPDATE ml_affiliate_session_health SET
                       fingerprint=?,state='unknown',failures=0,blocked_until=0,
                       last_status=0,last_error='',alert_sent=0,updated_at=?
                       WHERE id=1""",
                    (fingerprint, now),
                )
            return True, {"state": "unknown", "retry_after": 0}
        retry = max(0, int(row[3] - now + 0.999))
        return retry <= 0, {
            "state": row[1],
            "failures": int(row[2]),
            "retry_after": retry,
            "last_status": int(row[4]),
            "last_error": row[5],
        }

    def success(self, fingerprint):
        row = self._row()
        recovered = row[1] == "invalid"
        now = self.now()
        with self.db:
            self.db.execute(
                """UPDATE ml_affiliate_session_health SET
                   fingerprint=?,state='healthy',failures=0,blocked_until=0,
                   last_status=200,last_error='',last_success=?,
                   alert_sent=0,updated_at=? WHERE id=1""",
                (fingerprint, now, now),
            )
        if recovered:
            self.notifier(
                "✅ Radar de Ofertas: sessão de afiliados do Mercado Livre voltou a funcionar."
            )
        return recovered


    def failure(self, fingerprint, status, error):
        now = self.now()
        row = self._row()
        failures = 1
        already_alerted = False
        if row[0] == fingerprint:
            failures = int(row[2]) + 1
            already_alerted = bool(row[8])
        delays = (900, 3600, 14400, 21600)
        delay = delays[min(failures - 1, len(delays) - 1)]
        clean = " ".join(str(error or "").split())[:240]
        with self.db:
            self.db.execute(
                """UPDATE ml_affiliate_session_health SET
                   fingerprint=?,state='invalid',failures=?,blocked_until=?,
                   last_status=?,last_error=?,last_failure=?,alert_sent=?,
                   updated_at=? WHERE id=1""",
                (
                    fingerprint, failures, now + delay, int(status or 0),
                    clean, now, 1 if already_alerted else 0, now,
                ),
            )

        sent = False
        if not already_alerted:
            sent = self.notifier(
                "⚠️ Radar de Ofertas: a sessão de afiliados do Mercado Livre "
                f"foi recusada (HTTP {int(status or 0)}). "
                "As ofertas automáticas ML ficarão aguardando. "
                "Atualize ML_AFFILIATE_COOKIE e ML_AFFILIATE_CSRF no .env."
            )
            if sent:
                with self.db:
                    self.db.execute(
                        """UPDATE ml_affiliate_session_health
                           SET alert_sent=1,updated_at=? WHERE id=1""",
                        (self.now(),),
                    )
        return {
            "state": "invalid",
            "failures": failures,
            "retry_after": delay,
            "alert_sent": sent or already_alerted,
        }

    def status(self):
        row = self._row()
        retry = max(0, int(row[3] - self.now() + 0.999))
        return {
            "state": row[1],
            "failures": int(row[2]),
            "retry_after": retry,
            "last_status": int(row[4]),
            "last_error": row[5],
            "last_success": float(row[6]),
            "last_failure": float(row[7]),
            "alert_sent": bool(row[8]),
        }
