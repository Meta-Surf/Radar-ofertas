"""Amazon Brasil via Creators API (OAuth 2.0), sem scraping."""
import json
import os
import re
import time
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlparse

import requests

from ofertas_core import product
from shopee_afiliados import AffiliateError

MARKETPLACE = "www.amazon.com.br"
TOKEN_ENDPOINT = "https://api.amazon.com/auth/o2/token"
API_ENDPOINT = "https://creatorsapi.amazon/catalog/v1/getItems"
ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")
AMAZON_HOSTS = {"amazon.com.br", "www.amazon.com.br"}
IMAGE_HOSTS = {"m.media-amazon.com", "images-na.ssl-images-amazon.com"}
RESOURCES = [
    "images.primary.medium",
    "images.primary.large",
    "itemInfo.title",
    "offersV2.listings.availability",
    "offersV2.listings.condition",
    "offersV2.listings.isBuyBoxWinner",
    "offersV2.listings.price",
]


def valid_affiliate_url(url, partner_tag=""):
    if not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in AMAZON_HOSTS
            or parsed.username or parsed.password
            or parsed.port not in (None, 443)
            or not re.search(r"/(?:dp|gp/product)/[A-Z0-9]{10}(?:/|$)", parsed.path, re.I)
        ):
            return False
        if partner_tag:
            return partner_tag in parse_qs(parsed.query).get("tag", [])
        return True
    except (TypeError, ValueError):
        return False


def valid_image_url(url):
    try:
        parsed = urlparse(str(url or ""))
        return (
            parsed.scheme == "https"
            and parsed.hostname in IMAGE_HOSTS
            and not parsed.username and not parsed.password
            and parsed.port in (None, 443)
        )
    except ValueError:
        return False
def money_br(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise AffiliateError("Amazon: preço inválido na Creators API.") from None
    if not amount.is_finite() or amount <= 0 or amount.quantize(Decimal("0.01")) != amount:
        raise AffiliateError("Amazon: preço não é um valor monetário exato.")
    return f"{amount:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


class AmazonCreators:
    def __init__(self, partner_tag, credential_id, credential_secret,
                 transport=None, timeout=30):
        self.partner_tag = str(partner_tag or "").strip()
        self.credential_id = str(credential_id or "").strip()
        self.credential_secret = str(credential_secret or "").strip()
        self.transport = transport or requests
        self.timeout = max(5, int(timeout))
        self._access_token = ""
        self._token_until = 0.0
        if not re.fullmatch(r"[A-Za-z0-9._-]{3,80}", self.partner_tag):
            raise AffiliateError("Configure AMAZON_PARTNER_TAG com o Tracking ID do Associados Amazon Brasil.")
        if not self.credential_id or not self.credential_secret:
            raise AffiliateError(
                "Configure AMAZON_CREATORS_CREDENTIAL_ID e AMAZON_CREATORS_CREDENTIAL_SECRET."
            )

    @classmethod
    def from_env(cls):
        return cls(
            os.getenv("AMAZON_PARTNER_TAG", ""),
            os.getenv("AMAZON_CREATORS_CREDENTIAL_ID", ""),
            os.getenv("AMAZON_CREATORS_CREDENTIAL_SECRET", ""),
            timeout=os.getenv("AMAZON_CREATORS_TIMEOUT", "30"),
        )

    def _token(self):
        now = time.time()
        if self._access_token and now < self._token_until - 60:
            return self._access_token
        try:
            response = self.transport.post(
                TOKEN_ENDPOINT,
                headers={"Content-Type": "application/json"},
                json={
                    "grant_type": "client_credentials",
                    "client_id": self.credential_id,
                    "client_secret": self.credential_secret,
                    "scope": "creatorsapi::default",
                },
                timeout=(10, self.timeout),
            )
        except Exception:
            raise AffiliateError("Amazon Creators API: falha ao obter token OAuth.") from None
        if int(getattr(response, "status_code", 0)) != 200:
            raise AffiliateError(
                f"Amazon Creators API: autenticação recusada (HTTP {getattr(response, 'status_code', 0)})."
            )
        try:
            data = response.json()
        except Exception:
            raise AffiliateError("Amazon Creators API: resposta OAuth inválida.") from None
        token = str(data.get("access_token") or "")
        try:
            expires = int(data.get("expires_in") or 0)
        except (TypeError, ValueError):
            expires = 0
        if not token or expires < 60:
            raise AffiliateError("Amazon Creators API: token OAuth inválido.")
        self._access_token = token
        self._token_until = now + expires
        return token
    def request_item(self, asin):
        asin = str(asin or "").upper()
        if not ASIN_RE.fullmatch(asin):
            raise AffiliateError("Amazon: ASIN inválido.")
        body = {
            "itemIds": [asin],
            "itemIdType": "ASIN",
            "marketplace": MARKETPLACE,
            "partnerTag": self.partner_tag,
            "resources": RESOURCES,
        }
        headers = {
            "Authorization": "Bearer " + self._token(),
            "Content-Type": "application/json",
            "x-marketplace": MARKETPLACE,
        }
        try:
            response = self.transport.post(
                API_ENDPOINT, headers=headers, json=body, timeout=(10, self.timeout)
            )
        except Exception:
            raise AffiliateError("Amazon Creators API: falha de conexão.") from None
        status = int(getattr(response, "status_code", 0))
        if status in (401, 403):
            raise AffiliateError("Amazon Creators API: credenciais ou acesso recusados.")
        if status == 429:
            raise AffiliateError("Amazon Creators API: limite temporário de requisições.")
        if status != 200:
            raise AffiliateError(f"Amazon Creators API respondeu HTTP {status}.")
        try:
            data = response.json()
        except Exception:
            raise AffiliateError("Amazon Creators API retornou JSON inválido.") from None
        items = ((data.get("itemsResult") or {}).get("items") or []) if isinstance(data, dict) else []
        item = next(
            (x for x in items if isinstance(x, dict) and str(x.get("asin") or "").upper() == asin),
            None,
        )
        if not item:
            raise AffiliateError("Amazon: ASIN não encontrado na Creators API.")
        return self._parse_item(item, asin)

    def _parse_item(self, item, asin):
        detail = str(item.get("detailPageURL") or "")
        if not valid_affiliate_url(detail, self.partner_tag):
            raise AffiliateError("Amazon: Creators API não retornou link afiliado válido.")
        title = str((((item.get("itemInfo") or {}).get("title") or {}).get("displayValue") or "")).strip()
        if not title:
            raise AffiliateError("Amazon: título não retornado pela Creators API.")

        listings = ((item.get("offersV2") or {}).get("listings") or [])
        listings = [x for x in listings if isinstance(x, dict)]
        listing = next((x for x in listings if x.get("isBuyBoxWinner") is True), None)
        listing = listing or (listings[0] if listings else None)
        if not listing:
            raise AffiliateError("Amazon: produto sem oferta destacada disponível.")

        availability = str((listing.get("availability") or {}).get("type") or "").upper()
        if availability != "IN_STOCK":
            raise AffiliateError("Amazon: disponibilidade em estoque não confirmada.")
        condition = str((listing.get("condition") or {}).get("value") or "").lower()
        if condition and condition not in {"new", "novo"}:
            raise AffiliateError("Amazon: oferta destacada não é de produto novo.")
        price = (listing.get("price") or {}).get("money") or {}
        if str(price.get("currency") or "").upper() != "BRL":
            raise AffiliateError("Amazon: preço em BRL não retornado.")
        price_text = money_br(price.get("amount"))

        primary = (item.get("images") or {}).get("primary") or {}
        image = ""
        for size in ("large", "medium", "small"):
            candidate = (primary.get(size) or {}).get("url")
            if valid_image_url(candidate):
                image = candidate
                break
        if not image:
            raise AffiliateError("Amazon: imagem oficial não retornada.")
        return {
            "asin": asin,
            "name": title[:160],
            "price": price_text,
            "price_from": False,
            "price_condition": "Preço da oferta destacada confirmado pela Amazon Creators API.",
            "api_image": image,
            "affiliate_url": detail,
            "affiliate_generated": True,
            "stock_status": "IN_STOCK",
            "stock_confirmed": True,
        }
    def prepare(self, offer):
        identified = product(str(offer.get("url") or ""))
        if (
            not identified or identified[1] != "Amazon"
            or identified[0] != offer.get("product_id")
        ):
            raise AffiliateError("Amazon: produto divergente da URL captada.")
        asin = identified[0].split(":", 1)[1]
        fresh = self.request_item(asin)
        output = dict(offer)
        output.update(fresh)
        output["store"] = "Amazon"
        output["url"] = identified[2]
        output["source_date_api"] = time.time()
        return output


def main():
    import argparse
    from pathlib import Path
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env", encoding="utf-8-sig")
    parser = argparse.ArgumentParser(description="Diagnóstico Amazon Creators API; não publica.")
    parser.add_argument("--testar", required=True, metavar="URL_PRODUTO")
    args = parser.parse_args()
    try:
        found = product(args.testar)
        if not found or found[1] != "Amazon":
            raise AffiliateError("Use um link de produto Amazon Brasil com ASIN reconhecível.")
        client = AmazonCreators.from_env()
        item = client.request_item(found[0].split(":", 1)[1])
    except AffiliateError as error:
        parser.exit(1, str(error) + "\nNada foi publicado.\n")
    print("Produto:", item["name"])
    print("Preço:", item["price"])
    print("Estoque confirmado:", item["stock_confirmed"])
    print("Link afiliado válido:", valid_affiliate_url(item["affiliate_url"], client.partner_tag))
    print("Nada foi publicado.")

if __name__ == "__main__":
    main()
