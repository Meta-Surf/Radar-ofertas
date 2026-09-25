#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Radar de Ofertas Mercado Livre — somente monitoramento (API oficial / items bulk)

Esta versão NÃO usa /products/search para descobrir anúncios por palavra-chave.
Ela monitora:
  1) IDs/URLs informados em ML_ITEM_IDS;
  2) IDs/URLs colocados no arquivo ml_itens.txt;
  3) opcionalmente, anúncios ativos de vendedores em ML_SELLER_IDS.

Comandos:
    python radar_mercadolivre_v6.py --dry-run
    python radar_mercadolivre_v6.py
    python radar_mercadolivre_v6.py --loop
    python radar_mercadolivre_v6.py --item MLB1234567890

Variáveis obrigatórias no .env:
    ML_CLIENT_ID=
    ML_CLIENT_SECRET=
    ML_ACCESS_TOKEN=
    ML_REFRESH_TOKEN=

Variáveis opcionais:
    ML_SITE_ID=MLB
    ML_ITEM_IDS=MLB123...,MLB456...
    ML_SELLER_IDS=
    ML_MIN_DESCONTO=5
    ML_MAX_PUBLICACOES_CICLO=3
    ML_INTERVALO_RADAR=900
    ML_LIMITE_VENDEDOR=50
    ML_REPUBLICAR_QUEDA_PCT=5
    ML_MODO_TESTE=0
    EXIGIR_IMAGEM=1
    INTERVALO_PUBLICACOES=30

Arquivos opcionais:
    ml_itens.txt
        Uma URL ou ID do Mercado Livre por linha. O script extrai MLB123...
        automaticamente.

    ml_links_afiliados.json
        Mapeia item ID para o link de afiliado gerado oficialmente:
        {
          "MLB1234567890": "https://meli.la/xxxx"
        }

Observação:
    O Mercado Livre orienta gerar links de afiliado pelas ferramentas oficiais.
    Fase 0: publicação Telegram desativada, inclusive com link afiliado mapeado.
    Permalinks comuns são exibidos apenas no terminal para revisão manual.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
WATCHLIST_PATH = BASE_DIR / "ml_itens.txt"
AFFILIATE_PATH = BASE_DIR / "ml_links_afiliados.json"
STATE_PATH = BASE_DIR / "radar_ml_estado.json"

load_dotenv(ENV_PATH)

ML_API = "https://api.mercadolibre.com"
ML_SITE_ID = os.getenv("ML_SITE_ID", "MLB").strip()

ML_CLIENT_ID = os.getenv("ML_CLIENT_ID", "").strip()
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "").strip()
ML_ACCESS_TOKEN = os.getenv("ML_ACCESS_TOKEN", "").strip()
ML_REFRESH_TOKEN = os.getenv("ML_REFRESH_TOKEN", "").strip()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CANAL = os.getenv("TELEGRAM_CANAL", "").strip()

ML_ITEM_IDS_ENV = os.getenv("ML_ITEM_IDS", "").strip()
ML_SELLER_IDS_ENV = os.getenv("ML_SELLER_IDS", "").strip()

ML_MIN_DESCONTO = float(os.getenv("ML_MIN_DESCONTO", "5"))
ML_MAX_PUBLICACOES_CICLO = max(
    1, int(os.getenv("ML_MAX_PUBLICACOES_CICLO", "3"))
)
ML_INTERVALO_RADAR = max(60, int(os.getenv("ML_INTERVALO_RADAR", "900")))
ML_LIMITE_VENDEDOR = max(
    1, min(int(os.getenv("ML_LIMITE_VENDEDOR", "50")), 50)
)
ML_REPUBLICAR_QUEDA_PCT = max(
    0.0, float(os.getenv("ML_REPUBLICAR_QUEDA_PCT", "5"))
)
ML_MODO_TESTE = os.getenv("ML_MODO_TESTE", "0").strip() == "1"

EXIGIR_IMAGEM = os.getenv("EXIGIR_IMAGEM", "1").strip() == "1"
INTERVALO_PUBLICACOES = max(
    1, int(os.getenv("INTERVALO_PUBLICACOES", "30"))
)

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "RadarDeOfertas/2.0",
        "Accept": "application/json",
    }
)


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
        "ML_CLIENT_ID": ML_CLIENT_ID,
        "ML_CLIENT_SECRET": ML_CLIENT_SECRET,
        "ML_ACCESS_TOKEN": ML_ACCESS_TOKEN,
        "ML_REFRESH_TOKEN": ML_REFRESH_TOKEN,
    }

    faltando = [k for k, v in obrigatorias.items() if not v]

    if faltando:
        log("❌ Faltam variáveis no .env:")
        for nome in faltando:
            print(f"   - {nome}")
        sys.exit(1)


def atualizar_env(valores: dict[str, str]) -> None:
    linhas = (
        ENV_PATH.read_text(encoding="utf-8").splitlines()
        if ENV_PATH.exists()
        else []
    )

    encontradas: set[str] = set()
    saida: list[str] = []

    for linha in linhas:
        if not linha.strip() or linha.lstrip().startswith("#") or "=" not in linha:
            saida.append(linha)
            continue

        chave = linha.split("=", 1)[0].strip()

        if chave in valores:
            saida.append(f"{chave}={valores[chave]}")
            encontradas.add(chave)
        else:
            saida.append(linha)

    for chave, valor in valores.items():
        if chave not in encontradas:
            saida.append(f"{chave}={valor}")

    ENV_PATH.write_text("\n".join(saida) + "\n", encoding="utf-8")


def renovar_token() -> bool:
    global ML_ACCESS_TOKEN, ML_REFRESH_TOKEN

    log("🔄 Renovando token do Mercado Livre...")

    try:
        r = SESSION.post(
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
        log(f"❌ Erro de rede ao renovar token: {exc}")
        return False

    if r.status_code != 200:
        log(f"❌ Renovação falhou HTTP {r.status_code}: {r.text[:250]}")
        return False

    dados = r.json()
    novo_access = dados.get("access_token")
    novo_refresh = dados.get("refresh_token")

    if not novo_access or not novo_refresh:
        log("❌ OAuth não retornou os novos tokens.")
        return False

    ML_ACCESS_TOKEN = str(novo_access)
    ML_REFRESH_TOKEN = str(novo_refresh)

    atualizar_env(
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
    url = endpoint if endpoint.startswith("http") else f"{ML_API}{endpoint}"

    headers = dict(kwargs.pop("headers", {}) or {})
    headers["Authorization"] = f"Bearer {ML_ACCESS_TOKEN}"

    r = SESSION.request(
        method,
        url,
        headers=headers,
        timeout=30,
        **kwargs,
    )

    if r.status_code == 401 and retry_auth and renovar_token():
        return ml_request(
            method,
            endpoint,
            retry_auth=False,
            **kwargs,
        )

    if r.status_code == 403:
        log("⚠️ ML acesso_negado_403: causa não determinada pela resposta HTTP; "
            "confira recurso/ID, permissões e autorização. Não renovar token só por 403.")
    return r


ITEM_RE = re.compile(r"\b(MLB\d{6,})\b", re.IGNORECASE)
WID_RE = re.compile(r"(?:[?&#]|\b)(?:wid|item_id)=(MLB\d{6,})\b", re.IGNORECASE)


def extrair_item_id(texto: str) -> str | None:
    texto = texto or ""

    # Em URLs de produto de catálogo, o MLB do caminho /p/ pode ser
    # um PRODUCT_ID. O anúncio comprável normalmente aparece em wid=MLB...
    m = WID_RE.search(texto)
    if m:
        return m.group(1).upper()

    m = ITEM_RE.search(texto)
    return m.group(1).upper() if m else None


def ids_watchlist() -> set[str]:
    ids: set[str] = set()

    for parte in ML_ITEM_IDS_ENV.split(","):
        item_id = extrair_item_id(parte.strip())
        if item_id:
            ids.add(item_id)

    if WATCHLIST_PATH.exists():
        for linha in WATCHLIST_PATH.read_text(
            encoding="utf-8"
        ).splitlines():
            linha = linha.strip()
            if not linha or linha.startswith("#"):
                continue
            item_id = extrair_item_id(linha)
            if item_id:
                ids.add(item_id)

    return ids


def seller_ids() -> list[str]:
    return [
        s.strip()
        for s in ML_SELLER_IDS_ENV.split(",")
        if s.strip().isdigit()
    ]


def buscar_itens_vendedor(seller_id: str) -> list[str]:
    """
    Usa a busca pública por vendedor documentada pelo Mercado Livre.
    """
    try:
        r = ml_request(
            "GET",
            f"/sites/{ML_SITE_ID}/search",
            params={
                "seller_id": seller_id,
                "limit": ML_LIMITE_VENDEDOR,
            },
        )
    except requests.RequestException as exc:
        log(f"⚠️ Vendedor {seller_id}: erro de rede: {exc}")
        return []

    if r.status_code != 200:
        log(
            f"⚠️ Vendedor {seller_id}: HTTP {r.status_code}: "
            f"{r.text[:180]}"
        )
        return []

    resultados = r.json().get("results") or []
    ids = []

    for item in resultados:
        item_id = str(item.get("id") or "").upper()
        if item_id.startswith("MLB"):
            ids.append(item_id)

    return ids


def obter_item(item_id: str) -> dict[str, Any] | None:
    """
    Consulta item pelo endpoint público /items/bulk.

    Para o Radar de Ofertas precisamos consultar anúncios de outros vendedores.
    O endpoint bulk é o recurso atual documentado para consulta de múltiplos
    itens e retorna status individual por item.
    """
    try:
        r = ml_request(
            "GET",
            "/items/bulk",
            params={
                "ids": item_id,
            },
        )
    except requests.RequestException as exc:
        log(f"❌ {item_id}: erro de rede ao consultar item: {exc}")
        return None

    if r.status_code != 200:
        corpo = r.text.strip().replace(chr(10), " ")[:300]
        log(
            f"⚠️ {item_id}: GET /items/bulk -> "
            f"HTTP {r.status_code}: {corpo}"
        )
        return None

    try:
        dados = r.json()
    except ValueError:
        log(f"⚠️ {item_id}: resposta bulk não veio em JSON.")
        return None

    if not isinstance(dados, list) or not dados:
        log(f"⚠️ {item_id}: resposta bulk vazia.")
        return None

    registro = dados[0] or {}
    status_individual = (
        registro.get("status_code")
        if registro.get("status_code") is not None
        else registro.get("code")
    )

    if status_individual == 403:
        log(f"⚠️ {item_id}: acesso_negado_403 no resultado individual; causa não determinada.")
    if status_individual not in (200, 206):
        corpo = registro.get("body") or registro
        log(
            f"⚠️ {item_id}: /items/bulk status individual "
            f"{status_individual}: {str(corpo)[:300]}"
        )
        return None

    body = registro.get("body")

    if not isinstance(body, dict):
        log(f"⚠️ {item_id}: bulk não retornou body válido.")
        return None

    return body


def obter_preco(item_id: str) -> dict[str, Any] | None:
    """
    Primeiro tenta /sale_price. Se não estiver disponível, usa /prices.
    """
    try:
        r = ml_request(
            "GET",
            f"/items/{item_id}/sale_price",
            params={"context": "channel_marketplace"},
        )
    except requests.RequestException:
        r = None

    if r is not None and r.status_code == 200:
        try:
            d = r.json()
            amount = d.get("amount")
            regular = d.get("regular_amount")

            if amount is not None:
                return {
                    "amount": float(amount),
                    "regular_amount": (
                        float(regular)
                        if regular is not None
                        else None
                    ),
                    "promotion_id": d.get("promotion_id"),
                    "promotion_type": d.get("promotion_type"),
                }
        except (ValueError, TypeError):
            pass

    try:
        r2 = ml_request("GET", f"/items/{item_id}/prices")
    except requests.RequestException:
        return None

    if r2.status_code != 200:
        return None

    try:
        dados = r2.json()
    except ValueError:
        return None

    precos = dados.get("prices") or []

    promocionais = [
        p for p in precos
        if p.get("type") == "promotion"
        and p.get("amount") is not None
    ]

    padroes = [
        p for p in precos
        if p.get("type") == "standard"
        and p.get("amount") is not None
    ]

    atual = promocionais[0] if promocionais else (
        padroes[0] if padroes else None
    )

    if not atual:
        return None

    amount = float(atual["amount"])
    regular = atual.get("regular_amount")

    if regular is None and promocionais and padroes:
        regular = padroes[0].get("amount")

    return {
        "amount": amount,
        "regular_amount": (
            float(regular)
            if regular is not None
            else None
        ),
        "promotion_id": atual.get("id"),
        "promotion_type": atual.get("type"),
    }


def calcular_desconto(
    preco: float,
    regular: float | None,
) -> float:
    if not regular or regular <= preco:
        return 0.0
    return ((regular - preco) / regular) * 100.0


def imagem_item(item: dict[str, Any]) -> str | None:
    fotos = item.get("pictures") or []

    if fotos:
        p = fotos[0] or {}
        img = p.get("secure_url") or p.get("url")
        if img:
            return str(img)

    thumb = item.get("thumbnail")
    if thumb:
        return str(thumb).replace("http://", "https://", 1)

    return None


def carregar_links_afiliados() -> dict[str, str]:
    if not AFFILIATE_PATH.exists():
        return {}

    try:
        dados = json.loads(AFFILIATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

    if not isinstance(dados, dict):
        return {}

    return {
        str(k).upper(): str(v)
        for k, v in dados.items()
        if isinstance(v, str) and v.startswith("http")
    }


def montar_oferta(
    item_id: str,
    links_afiliados: dict[str, str],
) -> tuple[dict[str, Any] | None, str]:
    item = obter_item(item_id)

    if not item:
        return None, "item_indisponivel"

    if item.get("status") not in (None, "active"):
        return None, "item_inativo"

    preco = item.get("price")
    regular = item.get("original_price")

    try:
        preco = float(preco) if preco is not None else None
        regular = float(regular) if regular is not None else None
    except (TypeError, ValueError):
        return None, "preco_invalido"

    if preco is None or preco <= 0:
        return None, "sem_preco"

    desconto = calcular_desconto(preco, regular)

    if desconto < ML_MIN_DESCONTO:
        return None, "desconto_baixo"

    imagem = imagem_item(item)

    if EXIGIR_IMAGEM and not imagem:
        return None, "sem_imagem"

    permalink = item.get("permalink")

    if not permalink:
        return None, "sem_link"

    shipping = item.get("shipping") or {}

    link_saida = links_afiliados.get(item_id, str(permalink))

    return {
        "id": item_id,
        "title": item.get("title") or item_id,
        "price": preco,
        "regular_price": regular,
        "discount": desconto,
        "image": imagem,
        "permalink": str(permalink),
        "link": link_saida,
        "affiliate": item_id in links_afiliados,
        "free_shipping": bool(shipping.get("free_shipping")),
        "promotion_id": None,
        "promotion_type": None,
    }, "ok"


def carregar_estado() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}

    try:
        dados = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return dados if isinstance(dados, dict) else {}
    except Exception:
        return {}


def salvar_estado(estado: dict[str, Any]) -> None:
    STATE_PATH.write_text(
        json.dumps(estado, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def deve_publicar(
    oferta: dict[str, Any],
    estado: dict[str, Any],
) -> tuple[bool, str]:
    anterior = estado.get(oferta["id"])

    if not anterior:
        return True, "nova"

    preco_ant = anterior.get("price")
    desconto_ant = float(anterior.get("discount") or 0)
    preco_atual = float(oferta["price"])
    desconto_atual = float(oferta["discount"])

    try:
        preco_ant = float(preco_ant)
    except (TypeError, ValueError):
        return True, "sem_preco_anterior"

    limite = preco_ant * (1 - ML_REPUBLICAR_QUEDA_PCT / 100.0)

    if preco_atual <= limite:
        return True, "queda_de_preco"

    if desconto_atual >= desconto_ant + 5:
        return True, "desconto_aumentou"

    return False, "sem_mudanca_relevante"


def legenda(oferta: dict[str, Any]) -> str:
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

    if oferta.get("affiliate"):
        linhas.extend(["", "💰 Link de afiliado"])

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
    """Trava da Fase 0: nenhuma chamada ao Telegram, mesmo em uso direto."""
    log("🔒 Mercado Livre: publicação desativada; somente monitoramento manual.")
    return False


def coletar_ids() -> set[str]:
    ids = ids_watchlist()

    for seller_id in seller_ids():
        log(f"🏪 Consultando vendedor {seller_id}...")
        encontrados = buscar_itens_vendedor(seller_id)
        log(f"   {len(encontrados)} anúncio(s) recebido(s).")
        ids.update(encontrados)
        time.sleep(0.4)

    return ids


def ciclo(
    *,
    dry_run: bool = False,
    item_unico: str | None = None,
) -> None:
    log("🔒 Mercado Livre em modo monitoramento: nada será publicado no Telegram.")
    links_afiliados = carregar_links_afiliados()

    if item_unico:
        ids = {item_unico}
    else:
        ids = coletar_ids()

    if not ids:
        log("ℹ️ Nenhum item configurado para monitorar.")
        log("   Adicione IDs/URLs em ml_itens.txt ou ML_ITEM_IDS no .env.")
        return

    log(f"📡 Monitorando {len(ids)} item(ns).")

    ofertas: list[dict[str, Any]] = []
    diagnostico: dict[str, int] = {}

    for item_id in sorted(ids):
        oferta, motivo = montar_oferta(item_id, links_afiliados)

        if not oferta:
            diagnostico[motivo] = diagnostico.get(motivo, 0) + 1
            continue

        ofertas.append(oferta)
        diagnostico["oferta_valida"] = diagnostico.get("oferta_valida", 0) + 1

    ofertas.sort(
        key=lambda x: (
            float(x.get("discount") or 0),
            -float(x.get("price") or 0),
        ),
        reverse=True,
    )

    if diagnostico:
        resumo = ", ".join(
            f"{k}={v}"
            for k, v in sorted(
                diagnostico.items(),
                key=lambda kv: (-kv[1], kv[0]),
            )
        )
        log(f"📊 Diagnóstico: {resumo}")

    if not ofertas:
        log("ℹ️ Nenhuma oferta passou pelos filtros.")
        return

    escolhidas = ofertas[:ML_MAX_PUBLICACOES_CICLO]

    for oferta in escolhidas:
        af = " (link mapeado)" if oferta.get("affiliate") else ""
        log(
            f"🎯 {oferta['discount']:.0f}% OFF | "
            f"{moeda(oferta['price'])} | "
            f"{oferta['title'][:65]}{af}"
        )

        tipo_link = "mapeado pelo operador; comissão não verificada" if oferta.get("affiliate") else "comum; sem afiliação"
        print(f"   Link {tipo_link}: {oferta['link']}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Radar Mercado Livre: somente monitoramento, sem publicação."
    )
    p.add_argument("--dry-run", action="store_true", help="compatibilidade: todos os modos apenas monitoram")
    p.add_argument("--loop", action="store_true")
    p.add_argument("--item", type=str)
    p.add_argument(
        "--probe",
        type=str,
        help="consulta um item e mostra o HTTP/status sem publicar",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    validar_configuracao()

    if args.probe:
        probe_id = extrair_item_id(args.probe)
        if not probe_id:
            log("❌ Não encontrei um ID MLB válido em --probe.")
            sys.exit(1)

        log(f"🔬 Testando acesso público ao item {probe_id} via /items/bulk...")
        item = obter_item(probe_id)

        if not item:
            log("❌ O item não pôde ser consultado. Veja o HTTP acima.")
            return

        log("✅ Item acessível pela API.")
        log(f"   Título: {item.get('title')}")
        log(f"   Status: {item.get('status')}")
        log(f"   Site: {item.get('site_id')}")
        log(f"   Link: {item.get('permalink')}")
        return

    item_unico = None

    if args.item:
        item_unico = extrair_item_id(args.item)
        if not item_unico:
            log("❌ Não encontrei um ID MLB válido em --item.")
            sys.exit(1)

    if not args.loop:
        ciclo(
            dry_run=args.dry_run,
            item_unico=item_unico,
        )
        return

    log(
        f"♻️ Radar contínuo: ciclo a cada "
        f"{ML_INTERVALO_RADAR // 60} minuto(s)."
    )

    while True:
        try:
            ciclo(
                dry_run=args.dry_run,
                item_unico=item_unico,
            )
            log(
                f"💤 Próximo ciclo em "
                f"{ML_INTERVALO_RADAR} segundos."
            )
            time.sleep(ML_INTERVALO_RADAR)
        except KeyboardInterrupt:
            log("Radar encerrado.")
            break
        except Exception as exc:
            log(f"⚠️ Erro inesperado: {exc}")
            time.sleep(60)


if __name__ == "__main__":
    main()
