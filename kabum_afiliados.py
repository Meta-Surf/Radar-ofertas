"""Conversão de produtos KaBuM usando o Product Feed Awin já validado."""
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ofertas_core import product
from shopee_afiliados import AffiliateError


def valid_affiliate_url(url):
    """Aceita somente tracking links Awin do anunciante KaBuM."""
    try:
        parsed = urlparse(str(url or ""))
        query = parse_qs(parsed.query)
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == "www.awin1.com"
        and parsed.path == "/pclick.php"
        and "17729" in query.get("m", [])
        and bool(query.get("p"))
        and bool(query.get("a"))
    )


class KabumAffiliate:
    """Resolve merchant_product_id KaBuM para o aw_deep_link do feed local."""

    def __init__(self, db_path):
        self.db_path = Path(db_path)
        if not self.db_path.is_file():
            raise AffiliateError(
                "KaBuM/Awin indisponível: banco kabum_historico.sqlite3 não encontrado."
            )

    def lookup(self, merchant_product_id):
        merchant_product_id = str(merchant_product_id or "").strip()
        if not merchant_product_id.isdigit():
            raise AffiliateError("KaBuM: ID de produto inválido.")

        db = sqlite3.connect(str(self.db_path))
        try:
            db.execute("PRAGMA query_only=ON")
            row = db.execute(
                "SELECT affiliate_url FROM kabum_products WHERE product_id=?",
                (merchant_product_id,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise AffiliateError("KaBuM/Awin: falha ao consultar o catálogo local.") from exc
        finally:
            db.close()

        if not row:
            raise AffiliateError(
                f"KaBuM: produto {merchant_product_id} não consta no feed Awin atual; "
                "aguardando link de afiliado."
            )
        affiliate_url = str(row[0] or "").strip()
        if not valid_affiliate_url(affiliate_url):
            raise AffiliateError(
                f"KaBuM: link Awin inválido para o produto {merchant_product_id}."
            )
        return affiliate_url

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
        prepared["affiliate_url"] = self.lookup(merchant_product_id)
        prepared["affiliate_generated"] = True
        return prepared
