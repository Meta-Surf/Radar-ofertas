"""Conversão de produtos KaBuM usando Product Feed e Link Builder oficial da Awin."""
import os
import sqlite3
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

from ofertas_core import product
from shopee_afiliados import AffiliateError

ADVERTISER_ID = "17729"
AWIN_API = "https://api.awin.com"


def valid_affiliate_url(url):
    """Aceita tracking links HTTPS da Awin vinculados ao anunciante KaBuM."""
    try:
        parsed = urlparse(str(url or ""))
        query = parse_qs(parsed.query)
    except (TypeError, ValueError):
        return False
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in {
        "www.awin1.com", "awin1.com"
    }:
        return False
    advertiser = (
        query.get("m", []) + query.get("mid", []) + query.get("awinmid", [])
    )
    if ADVERTISER_ID not in advertiser:
        return False
    return parsed.path in {"/pclick.php", "/awclick.php", "/cread.php"}


class KabumAffiliate:
    """Resolve KaBuM para aw_deep_link local, com fallback opcional Link Builder."""

    def __init__(self, db_path, publisher_id="", access_token="", timeout=20):
        self.db_path = Path(db_path)
        self.publisher_id = str(publisher_id or "").strip()
        self.access_token = str(access_token or "").strip()
        self.timeout = max(5, int(timeout))
        if not self.db_path.is_file():
            raise AffiliateError(
                "KaBuM/Awin indisponível: banco kabum_historico.sqlite3 não encontrado."
            )

    @classmethod
    def from_env(cls, db_path):
        return cls(
            db_path,
            publisher_id=os.getenv("AWIN_PUBLISHER_ID", ""),
            access_token=os.getenv("AWIN_ACCESS_TOKEN", ""),
            timeout=os.getenv("AWIN_API_TIMEOUT", "20") or 20,
        )

    @property
    def link_builder_enabled(self):
        return self.publisher_id.isdigit() and bool(self.access_token)
    def _local_link(self, merchant_product_id):
        db = sqlite3.connect(str(self.db_path), timeout=20)
        try:
            row = db.execute(
                "SELECT affiliate_url FROM kabum_products WHERE product_id=?",
                (merchant_product_id,),
            ).fetchone()
            if not row:
                row = db.execute(
                    "SELECT affiliate_url FROM kabum_affiliate_cache WHERE product_id=?",
                    (merchant_product_id,),
                ).fetchone() if self._cache_table_exists(db) else None
        except sqlite3.Error as exc:
            raise AffiliateError("KaBuM/Awin: falha ao consultar o catálogo local.") from exc
        finally:
            db.close()
        link = str(row[0] or "").strip() if row else ""
        return link if valid_affiliate_url(link) else None

    @staticmethod
    def _cache_table_exists(db):
        return bool(db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='kabum_affiliate_cache'"
        ).fetchone())

    def _cache(self, product_id, destination_url, affiliate_url):
        db = sqlite3.connect(str(self.db_path), timeout=20)
        try:
            with db:
                db.execute(
                    """CREATE TABLE IF NOT EXISTS kabum_affiliate_cache(
                    product_id TEXT PRIMARY KEY,
                    destination_url TEXT NOT NULL,
                    affiliate_url TEXT NOT NULL,
                    generated_at REAL NOT NULL)"""
                )
                db.execute(
                    """INSERT INTO kabum_affiliate_cache VALUES(?,?,?,?)
                    ON CONFLICT(product_id) DO UPDATE SET
                      destination_url=excluded.destination_url,
                      affiliate_url=excluded.affiliate_url,
                      generated_at=excluded.generated_at""",
                    (product_id, destination_url, affiliate_url, time.time()),
                )
        finally:
            db.close()

    def _link_builder(self, destination_url):
        if not self.link_builder_enabled:
            raise AffiliateError(
                "KaBuM: produto fora do feed e Link Builder Awin não configurado."
            )
        endpoint = (
            f"{AWIN_API}/publishers/{self.publisher_id}/linkbuilder/generate"
        )
        payload = {
            "advertiserId": int(ADVERTISER_ID),
            "destinationUrl": destination_url,
            "parameters": {"clickref": "telegram"},
            "shorten": False,
        }
        try:
            response = requests.post(
                endpoint,
                json=payload,
                headers={"Authorization": f"Bearer {self.access_token}"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise AffiliateError("KaBuM/Awin Link Builder indisponível.") from exc
        if response.status_code != 200:
            raise AffiliateError(
                f"KaBuM/Awin Link Builder recusou a conversão (HTTP {response.status_code})."
            )
        try:
            link = str(response.json().get("url") or "").strip()
        except ValueError as exc:
            raise AffiliateError("KaBuM/Awin Link Builder retornou resposta inválida.") from exc
        if not valid_affiliate_url(link):
            raise AffiliateError("KaBuM/Awin Link Builder retornou link de tracking inválido.")
        return link

    def lookup(self, merchant_product_id, destination_url=None):
        merchant_product_id = str(merchant_product_id or "").strip()
        if not merchant_product_id.isdigit():
            raise AffiliateError("KaBuM: ID de produto inválido.")

        local = self._local_link(merchant_product_id)
        if local:
            return local

        destination_url = str(destination_url or "").strip()
        checked = product(destination_url)
        if not checked or checked[0] != f"KaBuM:{merchant_product_id}":
            raise AffiliateError(
                f"KaBuM: produto {merchant_product_id} não consta no feed Awin atual "
                "e não há destino seguro para o Link Builder."
            )
        generated = self._link_builder(checked[2])
        self._cache(merchant_product_id, checked[2], generated)
        return generated

    def prepare(self, offer):
        checked = product(offer.get("url", ""))
        if not checked or checked[1] != "KaBuM":
            raise AffiliateError("KaBuM: URL de produto inválida.")

        key, store, direct_url = checked
        if offer.get("product_id") != key or offer.get("url") != direct_url:
            raise AffiliateError("KaBuM: identidade do produto inconsistente.")

        merchant_product_id = key.split(":", 1)[1]
        prepared = dict(offer)
        prepared["store"] = store
        prepared["affiliate_url"] = self.lookup(merchant_product_id, direct_url)
        prepared["affiliate_generated"] = True
        return prepared
