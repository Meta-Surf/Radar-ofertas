"""Geração de links de afiliado Mercado Livre pela sessão autenticada do Link Builder.

Usa o endpoint interno empregado pela página de afiliados. Credenciais ficam somente
no .env local; nenhuma resposta crua com dados de sessão é exibida.
"""
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import requests

from ofertas_core import product, resolve
from shopee_afiliados import AffiliateError

ENDPOINT = "https://www.mercadolivre.com.br/affiliate-program/api/v2/affiliates/createLink"
LINKBUILDER = "https://www.mercadolivre.com.br/afiliados/linkbuilder"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
ML_HOSTS = {
    "meli.la",
    "mercadolivre.com",
    "www.mercadolivre.com",
    "mercadolivre.com.br",
    "www.mercadolivre.com.br",
    "produto.mercadolivre.com.br",
}


class MercadoLivreSessionError(AffiliateError):
    def __init__(self, status, message):
        self.status = int(status or 0)
        super().__init__(message)


def valid_affiliate_url(url):
    if not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname in ML_HOSTS
            and not parsed.username
            and not parsed.password
            and parsed.port in (None, 443)
            and parsed.path not in ("", "/")
            and not re.search(r"[\s\\\x00-\x1f]", url)
        )
    except ValueError:
        return False


def _parse_cookie_header(value):
    result = {}
    for piece in value.split(";"):
        piece = piece.strip()
        if not piece or "=" not in piece:
            continue
        name, content = piece.split("=", 1)
        name = name.strip()
        if name:
            result[name] = content
    return result


def _cookie_header(values):
    return "; ".join(f"{name}={value}" for name, value in values.items())


class MercadoLivreAffiliate:
    def __init__(
        self,
        cookie,
        csrf,
        tag,
        *,
        transport=None,
        user_agent=DEFAULT_USER_AGENT,
        refresh_cookies=True,
    ):
        self.cookie = str(cookie or "").strip()
        self.csrf = str(csrf or "").strip()
        self.tag = str(tag or "").strip()
        self.user_agent = str(user_agent or DEFAULT_USER_AGENT).strip()
        self.refresh_cookies = bool(refresh_cookies)
        self.transport = transport or requests
        self.cache = {}

        if "=" not in self.cookie or len(self.cookie) < 20:
            raise AffiliateError(
                "Preencha ML_AFFILIATE_COOKIE no .env com o Cookie completo da requisição createLink."
            )
        if not self.csrf or len(self.csrf) < 8 or re.search(r"[\s\x00-\x1f]", self.csrf):
            raise AffiliateError(
                "Preencha ML_AFFILIATE_CSRF no .env com o X-CSRF-Token da requisição createLink."
            )
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,80}", self.tag):
            raise AffiliateError(
                "Preencha ML_AFFILIATE_TAG no .env com a tag usada pelo seu Link Builder."
            )

    @classmethod
    def from_env(cls):
        refresh = os.getenv("ML_AFFILIATE_REFRESH_COOKIES", "1").strip() != "0"
        return cls(
            os.getenv("ML_AFFILIATE_COOKIE", ""),
            os.getenv("ML_AFFILIATE_CSRF", ""),
            os.getenv("ML_AFFILIATE_TAG", ""),
            user_agent=os.getenv("ML_AFFILIATE_USER_AGENT", DEFAULT_USER_AGENT),
            refresh_cookies=refresh,
        )

    def _base_headers(self):
        return {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            "User-Agent": self.user_agent,
        }

    def _refreshed_cookie(self):
        if not self.refresh_cookies:
            return self.cookie
        headers = self._base_headers()
        headers["Cookie"] = self.cookie
        try:
            response = self.transport.get(
                LINKBUILDER,
                headers=headers,
                timeout=(10, 20),
                allow_redirects=False,
            )
        except Exception:
            return self.cookie

        if not (200 <= int(getattr(response, "status_code", 0)) < 400):
            return self.cookie

        merged = _parse_cookie_header(self.cookie)
        try:
            updates = response.cookies.items()
        except Exception:
            updates = ()
        for name, value in updates:
            if name:
                merged[str(name)] = str(value)
        if merged:
            self.cookie = _cookie_header(merged)
        return self.cookie

    def tag_for_destination(self, destination="telegram"):
        from distribuicao import normalize_destination
        destination = normalize_destination(destination)
        if destination == "telegram":
            return self.tag
        env_name = "ML_AFFILIATE_TAG_" + destination.upper().replace("-", "_")
        value = str(os.getenv(env_name, "") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,80}", value):
            raise AffiliateError(
                f"Configure {env_name} antes de publicar Mercado Livre em {destination}."
            )
        return value

    def request(self, canonical_url, destination="telegram"):
        headers = self._base_headers()
        headers.update(
            {
                "Content-Type": "application/json",
                "Origin": "https://www.mercadolivre.com.br",
                "Referer": LINKBUILDER,
                "X-CSRF-Token": self.csrf,
                "Cookie": self._refreshed_cookie(),
            }
        )
        tag = self.tag_for_destination(destination)
        try:
            response = self.transport.post(
                ENDPOINT,
                headers=headers,
                json={"urls": [canonical_url], "tag": tag},
                timeout=(10, 30),
                allow_redirects=False,
            )
        except Exception:
            raise AffiliateError(
                "Falha de conexão com o gerador de links Mercado Livre; publicação bloqueada."
            ) from None

        status = int(getattr(response, "status_code", 0))
        if status in (401, 403):
            raise MercadoLivreSessionError(
                status,
                "Sessão de afiliado Mercado Livre recusada. Atualize Cookie e X-CSRF-Token no .env."
            )
        if status == 429:
            raise AffiliateError(
                "Mercado Livre limitou temporariamente a geração de links; publicação aguardará nova tentativa."
            )
        if status != 200:
            raise AffiliateError(
                f"Gerador de afiliados Mercado Livre respondeu HTTP {status}; publicação bloqueada."
            )
        try:
            data = response.json()
        except Exception:
            raise AffiliateError(
                "Gerador de afiliados Mercado Livre retornou resposta inválida; publicação bloqueada."
            ) from None
        if not isinstance(data, dict):
            raise AffiliateError(
                "Formato inesperado do gerador de afiliados Mercado Livre; publicação bloqueada."
            )
        return data

    def generate_link(self, url, destination="telegram"):
        from distribuicao import normalize_destination
        destination = normalize_destination(destination)
        identified = product(url)
        if not identified or identified[1] != "Mercado Livre":
            raise AffiliateError("É necessário um link reconhecido de produto Mercado Livre.")
        canonical = identified[2]
        cache_key = (canonical, destination)
        cached = self.cache.get(cache_key)
        if cached:
            return cached

        data = self.request(canonical, destination=destination)
        values = data.get("urls")
        if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict):
            raise AffiliateError(
                "Mercado Livre não devolveu um único link de afiliado; publicação bloqueada."
            )
        short = values[0].get("short_url")
        if not valid_affiliate_url(short) or short.rstrip("/") == canonical.rstrip("/"):
            raise AffiliateError(
                "Mercado Livre não devolveu um link de afiliado válido; publicação bloqueada."
            )
        self.cache[cache_key] = short
        return short

    def prepare(self, offer, destination="telegram"):
        if offer.get("kind") != "ml_offer" or offer.get("source") != "telegram":
            raise AffiliateError("Oferta Mercado Livre fora do fluxo automático autorizado.")
        identified = product(offer.get("url", ""))
        if (
            not identified
            or identified[1] != "Mercado Livre"
            or offer.get("product_id") != identified[0]
        ):
            raise AffiliateError("Produto Mercado Livre divergente da URL captada.")
        output = dict(offer, store="Mercado Livre")
        output["affiliate_url"] = self.generate_link(
            identified[2], destination=destination
        )
        output["affiliate_generated"] = True
        return output


def main():
    import argparse

    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env", encoding="utf-8-sig")
    parser = argparse.ArgumentParser(
        description="Gera um link de afiliado Mercado Livre sem publicar no Telegram."
    )
    parser.add_argument("--testar", required=True, metavar="URL_PRODUTO")
    args = parser.parse_args()
    try:
        client = MercadoLivreAffiliate.from_env()
        found = product(args.testar) or resolve(args.testar)
        if not found or found[1] != "Mercado Livre":
            raise AffiliateError(
                "Produto Mercado Livre não identificado. Use o link direto ou um meli.la que redirecione ao produto."
            )
        link = client.generate_link(found[2])
    except AffiliateError as error:
        parser.exit(1, str(error) + "\nNada foi publicado.\n")
    except Exception:
        parser.exit(1, "Não foi possível consultar o link Mercado Livre. Nada foi publicado.\n")

    print("Link de afiliado Mercado Livre gerado:")
    print(link)
    print("Nada foi publicado no Telegram.")


if __name__ == "__main__":
    main()
