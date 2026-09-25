#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Radar de Ofertas - Mercado Livre -> Telegram

Uso:
    python radar_mercadolivre.py
        Faz uma varredura única.

    python radar_mercadolivre.py --loop
        Mantém o radar rodando continuamente.

    python radar_mercadolivre.py --dry-run
        Busca e mostra ofertas, mas não publica no Telegram.

    python radar_mercadolivre.py --item MLB1234567890
        Testa/publica um item específico.

    python radar_mercadolivre.py --query "placa de video"
        Faz uma varredura única usando somente essa busca.

Variáveis obrigatórias no .env:
    TELEGRAM_TOKEN=
    TELEGRAM_CANAL=
    ML_CLIENT_ID=
    ML_CLIENT_SECRET=
    ML_ACCESS_TOKEN=
    ML_REFRESH_TOKEN=

Variáveis opcionais:
    ML_SITE_ID=MLB
    ML_QUERIES=placa de video,ssd nvme,notebook gamer,monitor gamer,smartphone
    ML_MIN_DESCONTO=10
    ML_PRECO_MIN=0
    ML_PRECO_MAX=0
    ML_LIMITE_BUSCA=30
    ML_MAX_PUBLICACOES_CICLO=3
    ML_INTERVALO_RADAR=900
    ML_SOMENTE_NOVOS=1
    ML_MODO_TESTE=0
    EXIGIR_IMAGEM=1
    INTERVALO_PUBLICACOES=30

Observação:
    O link publicado é o permalink normal retornado pelo Mercado Livre.
    A geração/conversão para link de afiliado deve ser integrada separadamente
    quando houver um método oficial disponível para sua conta/programa.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


# ---------------------------------------------------------------------------
# Caminhos
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
STATE_PATH = BASE_DIR / "radar_ml_publicados.json"

load_dotenv(ENV_PATH)


# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

ML_API = "https://api.mercadolibre.com"
ML_SITE_ID = os.getenv("ML_SITE_ID", "MLB").strip()

ML_CLIENT_ID = os.getenv("ML_CLIENT_ID", "").strip()
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "").strip()
ML_ACCESS_TOKEN = os.getenv("ML_ACCESS_TOKEN", "").strip()
ML_REFRESH_TOKEN = os.getenv("ML_REFRESH_TOKEN", "").strip()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CANAL = os.getenv("TELEGRAM_CANAL", "").strip()

EXIGIR_IMAGEM = os.getenv("EXIGIR_IMAGEM", "1").strip() == "1"
INTERVALO_PUBLICACOES = int(os.getenv("INTERVALO_PUBLICACOES", "30"))

ML_MIN_DESCONTO = float(os.getenv("ML_MIN_DESCONTO", "10"))
ML_PRECO_MIN = float(os.getenv("ML_PRECO_MIN", "0"))
ML_PRECO_MAX = float(os.getenv("ML_PRECO_MAX", "0"))
ML_LIMITE_BUSCA = max(1, min(int(os.getenv("ML_LIMITE_BUSCA", "30")), 50))
ML_MAX_PUBLICACOES_CICLO = max(
    1, int(os.getenv("ML_MAX_PUBLICACOES_CICLO", "3"))
)
ML_INTERVALO_RADAR = max(60, int(os.getenv("ML_INTERVALO_RADAR", "900")))
ML_SOMENTE_NOVOS = os.getenv("ML_SOMENTE_NOVOS", "1").strip() == "1"
ML_MODO_TESTE = os.getenv("ML_MODO_TESTE", "0").strip() == "1"

DEFAULT_QUERIES = [
    "placa de video",
    "ssd nvme",
    "notebook gamer",
    "monitor gamer",
    "smartphone",
]

QUERIES = [
    q.strip()
    for q in os.getenv("ML_QUERIES", ",".join(DEFAULT_QUERIES)).split(",")
    if q.strip()
]

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "RadarDeOfertas/1.0",
        "Accept": "application/json",
    }
)


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    agora = datetime.now().strftime("%H:%M:%S")
    print(f"[{agora}] {msg}", flush=True)


def moeda(valor: float | int | None) -> str:
    if valor is None:
        return "-"
    return (
        f"R$ {float(valor):,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


def validar_configuracao() -> None:
    obrigatorias = {
        "TELEGRAM_TOKEN": TELEGRAM_TOKEN,
        "TELEGRAM_CANAL": TELEGRAM_CANAL,
        "ML_CLIENT_ID": ML_CLIENT_ID,
        "ML_CLIENT_SECRET": ML_CLIENT_SECRET,
        "ML_ACCESS_TOKEN": ML_ACCESS_TOKEN,
        "ML_REFRESH_TOKEN": ML_REFRESH_TOKEN,
    }

    faltando = [nome for nome, valor in obrigatorias.items() if not valor]

    if faltando:
        log("❌ Variáveis ausentes no .env:")
        for nome in faltando:
            print(f"   - {nome}")
        sys.exit(1)


def atualizar_variaveis_env(valores: dict[str, str]) -> None:
    """
    Atualiza somente as chaves informadas no .env, preservando as demais linhas.
    Isso é usado após a renovação OAuth do Mercado Livre.
    """
    linhas: list[str] = []

    if ENV_PATH.exists():
        linhas = ENV_PATH.read_text(encoding="utf-8").splitlines()

    encontradas: set[str] = set()
    novas_linhas: list[str] = []

    for linha in linhas:
        stripped = linha.strip()

        if not stripped or stripped.startswith("#") or "=" not in linha:
            novas_linhas.append(linha)
            continue

        chave = linha.split("=", 1)[0].strip()

        if chave in valores:
            novas_linhas.append(f"{chave}={valores[chave]}")
            encontradas.add(chave)
        else:
            novas_linhas.append(linha)

    for chave, valor in valores.items():
        if chave not in encontradas:
            novas_linhas.append(f"{chave}={valor}")

    ENV_PATH.write_text("\n".join(novas_linhas) + "\n", encoding="utf-8")


def carregar_publicados() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}

    try:
        dados = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return dados if isinstance(dados, dict) else {}
    except Exception:
        return {}


def salvar_publicados(dados: dict[str, Any]) -> None:
    # Mantém o arquivo de estado em tamanho razoável.
    if len(dados) > 3000:
        itens = list(dados.items())[-2500:]
        dados = dict(itens)

    STATE_PATH.write_text(
        json.dumps(dados, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# OAuth / Mercado Livre
# ---------------------------------------------------------------------------

def renovar_token() -> bool:
    global ML_ACCESS_TOKEN, ML_REFRESH_TOKEN

    log("🔄 Access token expirado. Renovando token do Mercado Livre...")

    try:
        resposta = SESSION.post(
            f"{ML_API}/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": ML_CLIENT_ID,
                "client_secret": ML_CLIENT_SECRET,
                "refresh_token": ML_REFRESH_TOKEN,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
    except requests.RequestException as exc:
        log(f"❌ Falha de rede ao renovar token: {exc}")
        return False

    if resposta.status_code != 200:
        log(f"❌ Não foi possível renovar o token ({resposta.status_code}).")
        try:
            erro = resposta.json()
            log(f"   {erro.get('message') or erro.get('error') or erro}")
        except Exception:
            log(f"   {resposta.text[:300]}")
        return False

    dados = resposta.json()

    novo_access = dados.get("access_token")
    novo_refresh = dados.get("refresh_token")

    if not novo_access or not novo_refresh:
        log("❌ Resposta OAuth não trouxe access_token/refresh_token.")
        return False

    ML_ACCESS_TOKEN = str(novo_access)
    ML_REFRESH_TOKEN = str(novo_refresh)

    atualizar_variaveis_env(
        {
            "ML_ACCESS_TOKEN": ML_ACCESS_TOKEN,
            "ML_REFRESH_TOKEN": ML_REFRESH_TOKEN,
        }
    )

    log("✅ Token renovado e .env atualizado.")
    return True


def ml_request(
    method: str,
    endpoint: str,
    *,
    retry_auth: bool = True,
    **kwargs: Any,
) -> requests.Response:
    headers = dict(kwargs.pop("headers", {}) or {})
    headers["Authorization"] = f"Bearer {ML_ACCESS_TOKEN}"

    url = endpoint if endpoint.startswith("http") else f"{ML_API}{endpoint}"

    resposta = SESSION.request(
        method,
        url,
        headers=headers,
        timeout=30,
        **kwargs,
    )

    if resposta.status_code == 401 and retry_auth:
        if renovar_token():
            return ml_request(
                method,
                endpoint,
                retry_auth=False,
                **kwargs,
            )

    return resposta


# ---------------------------------------------------------------------------
# Busca e avaliação de ofertas
# ---------------------------------------------------------------------------

def buscar_produtos_catalogo(query: str) -> list[dict[str, Any]]:
    """
    Busca produtos de catálogo do Mercado Livre usando /products/search.

    A busca geral por palavra-chave em /sites/{site}/search pode retornar 403
    para aplicações atuais. O fluxo abaixo usa o buscador oficial de produtos
    de catálogo e, depois, consulta o buy_box_winner de cada produto.
    """
    try:
        resposta = ml_request(
            "GET",
            "/products/search",
            params={
                "status": "active",
                "site_id": ML_SITE_ID,
                "q": query,
                "limit": ML_LIMITE_BUSCA,
            },
        )
    except requests.RequestException as exc:
        log(f"❌ Erro de rede na busca '{query}': {exc}")
        return []

    if resposta.status_code != 200:
        log(
            f"⚠️ Busca '{query}' retornou HTTP {resposta.status_code}: "
            f"{resposta.text[:220]}"
        )
        return []

    dados = resposta.json()
    resultados = dados.get("results", [])

    if not isinstance(resultados, list):
        return []

    return resultados


def obter_produto_catalogo(product_id: str) -> dict[str, Any] | None:
    try:
        resposta = ml_request("GET", f"/products/{product_id}")
    except requests.RequestException:
        return None

    if resposta.status_code != 200:
        return None

    return resposta.json()


def obter_item(item_id: str) -> dict[str, Any] | None:
    try:
        resposta = ml_request("GET", f"/items/{item_id}")
    except requests.RequestException:
        return None

    if resposta.status_code != 200:
        return None

    return resposta.json()


def obter_preco_venda(item_id: str) -> dict[str, Any] | None:
    """
    Endpoint recomendado pelo Mercado Livre para consultar o preço de venda
    atual no canal marketplace.
    """
    try:
        resposta = ml_request(
            "GET",
            f"/items/{item_id}/sale_price",
            params={"context": "channel_marketplace"},
        )
    except requests.RequestException:
        return None

    if resposta.status_code != 200:
        return None

    return resposta.json()


def montar_oferta_catalogo(
    produto_resumo: dict[str, Any],
) -> dict[str, Any] | None:
    """
    Converte um produto de catálogo na publicação vencedora atual (buy box).
    """
    product_id = str(produto_resumo.get("id") or "").strip()

    if not product_id:
        return None

    produto = obter_produto_catalogo(product_id)
    if not produto:
        return None

    winner = produto.get("buy_box_winner") or {}
    item_id = str(winner.get("item_id") or "").strip()

    # Alguns produtos de catálogo não têm publicação vencedora no momento.
    if not item_id:
        return None

    detalhe = obter_item(item_id)
    if not detalhe:
        return None

    status = detalhe.get("status")
    if status and status != "active":
        return None

    condition = detalhe.get("condition")
    if ML_SOMENTE_NOVOS and condition and condition != "new":
        return None

    sale_price = obter_preco_venda(item_id) or {}

    preco = sale_price.get("amount")
    regular = sale_price.get("regular_amount")

    if preco is None:
        preco = winner.get("price")
    if preco is None:
        preco = detalhe.get("price")

    if regular is None:
        regular = detalhe.get("original_price")

    try:
        preco = float(preco) if preco is not None else None
        regular = float(regular) if regular is not None else None
    except (TypeError, ValueError):
        return None

    if preco is None or preco <= 0:
        return None

    if ML_PRECO_MIN > 0 and preco < ML_PRECO_MIN:
        return None

    if ML_PRECO_MAX > 0 and preco > ML_PRECO_MAX:
        return None

    desconto = calcular_desconto(preco, regular)

    if desconto < ML_MIN_DESCONTO:
        return None

    shipping = (
        winner.get("shipping")
        or detalhe.get("shipping")
        or {}
    )

    titulo = (
        detalhe.get("title")
        or produto.get("name")
        or produto_resumo.get("name")
        or "Oferta"
    )

    permalink = (
        detalhe.get("permalink")
        or produto.get("permalink")
    )

    imagem = imagem_do_item(detalhe)

    # Fallback para imagem do produto de catálogo.
    if not imagem:
        pictures = produto.get("pictures") or []
        if pictures:
            p = pictures[0] or {}
            imagem = p.get("secure_url") or p.get("url")

    if not permalink:
        return None

    if EXIGIR_IMAGEM and not imagem:
        return None

    return {
        "id": item_id,
        "catalog_product_id": product_id,
        "title": titulo,
        "price": preco,
        "regular_price": regular,
        "discount": desconto,
        "permalink": permalink,
        "image": imagem,
        "free_shipping": bool(shipping.get("free_shipping")),
        "condition": condition,
        "available_quantity": (
            winner.get("available_quantity")
            if winner.get("available_quantity") is not None
            else detalhe.get("available_quantity")
        ),
    }


def calcular_desconto(preco: float | None, regular: float | None) -> float:
    if not preco or not regular:
        return 0.0

    if regular <= preco:
        return 0.0

    return ((regular - preco) / regular) * 100.0


def imagem_do_item(
    detalhe: dict[str, Any],
    resultado: dict[str, Any] | None = None,
) -> str | None:
    fotos = detalhe.get("pictures") or []

    if fotos:
        primeira = fotos[0] or {}
        imagem = primeira.get("secure_url") or primeira.get("url")
        if imagem:
            return str(imagem)

    if resultado:
        imagem = resultado.get("thumbnail")
        if imagem:
            # O endpoint de busca costuma devolver thumbnail HTTP em alguns casos.
            return str(imagem).replace("http://", "https://", 1)

    return None


def montar_oferta(
    resultado: dict[str, Any],
) -> dict[str, Any] | None:
    item_id = str(resultado.get("id") or "").strip()

    if not item_id:
        return None

    detalhe = obter_item(item_id)
    if not detalhe:
        return None

    status = detalhe.get("status")
    if status and status != "active":
        return None

    condition = detalhe.get("condition") or resultado.get("condition")

    if ML_SOMENTE_NOVOS and condition and condition != "new":
        return None

    sale_price = obter_preco_venda(item_id) or {}

    preco = sale_price.get("amount")
    regular = sale_price.get("regular_amount")

    # Fallback temporário para compatibilidade quando sale_price não estiver
    # disponível para determinado item/contexto.
    if preco is None:
        preco = resultado.get("price")
    if preco is None:
        preco = detalhe.get("price")

    if regular is None:
        regular = resultado.get("original_price")
    if regular is None:
        regular = detalhe.get("original_price")

    try:
        preco = float(preco) if preco is not None else None
        regular = float(regular) if regular is not None else None
    except (TypeError, ValueError):
        return None

    if preco is None or preco <= 0:
        return None

    if ML_PRECO_MIN > 0 and preco < ML_PRECO_MIN:
        return None

    if ML_PRECO_MAX > 0 and preco > ML_PRECO_MAX:
        return None

    desconto = calcular_desconto(preco, regular)

    if desconto < ML_MIN_DESCONTO:
        return None

    shipping = detalhe.get("shipping") or resultado.get("shipping") or {}

    oferta = {
        "id": item_id,
        "title": detalhe.get("title") or resultado.get("title") or "Oferta",
        "price": preco,
        "regular_price": regular,
        "discount": desconto,
        "permalink": detalhe.get("permalink")
        or resultado.get("permalink"),
        "image": imagem_do_item(detalhe, resultado),
        "free_shipping": bool(shipping.get("free_shipping")),
        "condition": condition,
        "available_quantity": detalhe.get("available_quantity"),
    }

    if not oferta["permalink"]:
        return None

    if EXIGIR_IMAGEM and not oferta["image"]:
        return None

    return oferta


def detalhar_item_por_id(item_id: str) -> dict[str, Any] | None:
    detalhe = obter_item(item_id)
    if not detalhe:
        return None

    # Reaproveita montar_oferta criando um resultado mínimo.
    resultado = {
        "id": item_id,
        "title": detalhe.get("title"),
        "price": detalhe.get("price"),
        "original_price": detalhe.get("original_price"),
        "permalink": detalhe.get("permalink"),
        "thumbnail": detalhe.get("thumbnail"),
        "condition": detalhe.get("condition"),
        "shipping": detalhe.get("shipping"),
    }

    # Para teste manual, não bloqueamos pelo desconto mínimo.
    sale_price = obter_preco_venda(item_id) or {}

    preco = sale_price.get("amount")
    regular = sale_price.get("regular_amount")

    if preco is None:
        preco = detalhe.get("price")
    if regular is None:
        regular = detalhe.get("original_price")

    if preco is None:
        return None

    try:
        preco_f = float(preco)
        regular_f = float(regular) if regular is not None else None
    except (TypeError, ValueError):
        return None

    shipping = detalhe.get("shipping") or {}

    return {
        "id": item_id,
        "title": detalhe.get("title") or "Oferta",
        "price": preco_f,
        "regular_price": regular_f,
        "discount": calcular_desconto(preco_f, regular_f),
        "permalink": detalhe.get("permalink"),
        "image": imagem_do_item(detalhe, resultado),
        "free_shipping": bool(shipping.get("free_shipping")),
        "condition": detalhe.get("condition"),
        "available_quantity": detalhe.get("available_quantity"),
    }


def buscar_ofertas(queries: list[str]) -> list[dict[str, Any]]:
    publicados = carregar_publicados()
    vistos_produtos: set[str] = set()
    vistos_itens: set[str] = set()
    ofertas: list[dict[str, Any]] = []

    for query in queries:
        log(f"🔎 Buscando produto de catálogo: {query}")
        resultados = buscar_produtos_catalogo(query)
        log(f"   {len(resultados)} produto(s) de catálogo recebido(s).")

        for produto_resumo in resultados:
            product_id = str(produto_resumo.get("id") or "")

            if not product_id or product_id in vistos_produtos:
                continue

            vistos_produtos.add(product_id)

            oferta = montar_oferta_catalogo(produto_resumo)

            if not oferta:
                continue

            item_id = str(oferta.get("id") or "")

            if (
                not item_id
                or item_id in vistos_itens
                or item_id in publicados
            ):
                continue

            vistos_itens.add(item_id)
            ofertas.append(oferta)

            log(
                f"   ✅ {oferta['discount']:.0f}% OFF | "
                f"{moeda(oferta['price'])} | "
                f"{oferta['title'][:65]}"
            )

            time.sleep(0.15)

        # Pequena pausa para evitar rajadas desnecessárias na API.
        time.sleep(0.4)

    ofertas.sort(
        key=lambda x: (
            float(x.get("discount") or 0),
            bool(x.get("free_shipping")),
        ),
        reverse=True,
    )

    return ofertas


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def legenda_telegram(oferta: dict[str, Any]) -> str:
    titulo = html.escape(str(oferta["title"]))
    preco = float(oferta["price"])
    regular = oferta.get("regular_price")
    desconto = float(oferta.get("discount") or 0)

    linhas = [
        "📡 <b>RADAR DE OFERTAS</b>",
        "",
        f"🔥 <b>{titulo}</b>",
        "",
    ]

    if regular and float(regular) > preco:
        linhas.append(f"❌ De: <s>{moeda(float(regular))}</s>")

    linhas.append(f"✅ <b>Por: {moeda(preco)}</b>")

    if desconto > 0:
        linhas.append(f"🔥 <b>{desconto:.0f}% OFF</b>")

    if oferta.get("free_shipping"):
        linhas.extend(["", "🚚 <b>Frete grátis</b>"])

    linhas.extend(
        [
            "",
            "⚡ Preço e disponibilidade podem mudar.",
            "",
            "👇 <b>Confira a oferta</b>",
        ]
    )

    return "\n".join(linhas)


def enviar_telegram(oferta: dict[str, Any]) -> bool:
    link = str(oferta["permalink"])
    imagem = oferta.get("image")
    legenda = legenda_telegram(oferta)

    teclado = json.dumps(
        {
            "inline_keyboard": [
                [
                    {
                        "text": "🛒 VER OFERTA",
                        "url": link,
                    }
                ]
            ]
        },
        ensure_ascii=False,
    )

    if imagem:
        endpoint = (
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
        )

        payload = {
            "chat_id": TELEGRAM_CANAL,
            "photo": imagem,
            "caption": legenda,
            "parse_mode": "HTML",
            "reply_markup": teclado,
        }

        try:
            resposta = SESSION.post(endpoint, data=payload, timeout=30)
        except requests.RequestException as exc:
            log(f"❌ Erro de rede no Telegram: {exc}")
            return False

        if resposta.status_code == 200:
            return True

        log(
            f"⚠️ sendPhoto falhou ({resposta.status_code}). "
            "Tentando publicar sem foto..."
        )

    endpoint = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CANAL,
        "text": legenda,
        "parse_mode": "HTML",
        "reply_markup": teclado,
        "disable_web_page_preview": False,
    }

    try:
        resposta = SESSION.post(endpoint, data=payload, timeout=30)
    except requests.RequestException as exc:
        log(f"❌ Erro de rede no Telegram: {exc}")
        return False

    if resposta.status_code != 200:
        log(f"❌ Telegram HTTP {resposta.status_code}: {resposta.text[:300]}")
        return False

    return True


def registrar_publicacao(oferta: dict[str, Any]) -> None:
    publicados = carregar_publicados()

    publicados[str(oferta["id"])] = {
        "published_at": datetime.now(timezone.utc).isoformat(),
        "title": oferta.get("title"),
        "price": oferta.get("price"),
        "discount": oferta.get("discount"),
    }

    salvar_publicados(publicados)


def publicar_ofertas(
    ofertas: list[dict[str, Any]],
    *,
    dry_run: bool = False,
) -> int:
    if not ofertas:
        log("ℹ️ Nenhuma oferta nova passou pelos filtros.")
        return 0

    selecionadas = ofertas[:ML_MAX_PUBLICACOES_CICLO]

    log(
        f"🎯 {len(ofertas)} oferta(s) válida(s). "
        f"Publicando até {len(selecionadas)} neste ciclo."
    )

    publicadas = 0

    for oferta in selecionadas:
        log(
            f"📦 {oferta['discount']:.0f}% OFF | "
            f"{moeda(oferta['price'])} | {oferta['title'][:70]}"
        )

        if dry_run:
            print(f"   {oferta['permalink']}")
            continue

        if enviar_telegram(oferta):
            registrar_publicacao(oferta)
            publicadas += 1
            log("✅ Publicada no Telegram.")
        else:
            log("❌ Não foi possível publicar.")

        if oferta is not selecionadas[-1]:
            time.sleep(max(1, INTERVALO_PUBLICACOES))

    return publicadas


# ---------------------------------------------------------------------------
# Execução
# ---------------------------------------------------------------------------

def ciclo(
    queries: list[str],
    *,
    dry_run: bool = False,
) -> None:
    log("📡 RADAR DE OFERTAS - MERCADO LIVRE")
    log(
        f"Filtros: mínimo {ML_MIN_DESCONTO:.0f}% OFF | "
        f"até {ML_MAX_PUBLICACOES_CICLO} publicação(ões)/ciclo"
    )

    ofertas = buscar_ofertas(queries)
    publicar_ofertas(ofertas, dry_run=dry_run)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Radar de ofertas Mercado Livre -> Telegram"
    )

    parser.add_argument(
        "--loop",
        action="store_true",
        help="mantém o radar rodando continuamente",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="não publica; apenas mostra as ofertas encontradas",
    )

    parser.add_argument(
        "--query",
        type=str,
        help="faz a busca usando somente esta expressão",
    )

    parser.add_argument(
        "--item",
        type=str,
        help="testa/publica um item específico, ex.: MLB1234567890",
    )

    return parser.parse_args()


def main() -> None:
    validar_configuracao()
    args = parse_args()

    dry_run = bool(args.dry_run or ML_MODO_TESTE)

    if args.item:
        item_id = args.item.strip().upper()
        log(f"🔎 Consultando item {item_id}...")

        oferta = detalhar_item_por_id(item_id)

        if not oferta:
            log("❌ Não foi possível consultar esse item.")
            sys.exit(1)

        log(
            f"✅ {oferta['title']} | "
            f"{moeda(oferta['price'])} | "
            f"{oferta['discount']:.0f}% OFF"
        )

        publicar_ofertas([oferta], dry_run=dry_run)
        return

    queries = [args.query.strip()] if args.query else QUERIES

    if not queries:
        log("❌ Nenhuma busca configurada em ML_QUERIES.")
        sys.exit(1)

    if not args.loop:
        ciclo(queries, dry_run=dry_run)
        return

    log(
        f"♻️ Modo contínuo ativado. Nova varredura a cada "
        f"{ML_INTERVALO_RADAR // 60} minuto(s)."
    )

    while True:
        try:
            ciclo(queries, dry_run=dry_run)
        except KeyboardInterrupt:
            log("Radar encerrado pelo usuário.")
            break
        except Exception as exc:
            log(f"⚠️ Erro inesperado no ciclo: {exc}")

        log(f"💤 Próxima varredura em {ML_INTERVALO_RADAR} segundos.")

        try:
            time.sleep(ML_INTERVALO_RADAR)
        except KeyboardInterrupt:
            log("Radar encerrado pelo usuário.")
            break


if __name__ == "__main__":
    main()
