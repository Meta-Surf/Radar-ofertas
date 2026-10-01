"""Radar KaBuM via Product Data Feed da Awin.

Aceita:
1) URL direta do Product Feed da Awin; ou
2) URL "Download da Lista de Feed" da Awin.

Se receber a lista de feeds, o programa localiza automaticamente o feed KaBuM
(Advertiser 17729 / Feed 46967 por padrão), baixa o Product Feed real, valida,
salva cache local e atualiza o histórico de preços.

A execução manual sem --enfileirar continua diagnóstica. Em produção, o módulo
alimenta a fila persistente do publicador unificado; ele nunca envia diretamente.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import re
import sqlite3
import time
import unicodedata
import urllib.error
import urllib.request
import zipfile
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from pathlib import Path

DB_NAME = "kabum_historico.sqlite3"
DEFAULT_CACHE = "kabum_feed_atual.csv.gz"
USER_AGENT = "RadarOfertas-Kabum/2.0"
HISTORY_WINDOWS = tuple(range(15, 181, 15))
DAY = 86400
PRIORITY_BRANDS = ("jbl", "nvidia", "amd")
PRIORITY_CATEGORY_TERMS = (
    "placa de video", "placas de video", "gpu", "processador", "processadores", "cpu",
    "notebook", "monitor", "smartphone", "celular", "tablet", "ssd",
    "memoria ram", "televisor", "tv",
)
LOW_PRIORITY_CATEGORY_TERMS = (
    "suporte", "cabo", "adaptador", "case", "capa", "pelicula", "mousepad",
    "organizador", "extensao",
)

PRODUCT_REQUIRED = {
    "aw_deep_link",
    "product_name",
    "merchant_product_id",
    "search_price",
}


def norm(value):
    text = str(value or "").replace("\ufeff", "").strip()
    text = "".join(
        c for c in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(c)
    )
    return re.sub(r"\s+", " ", text).lower()


def money(value):
    """Converte preço decimal do feed em centavos, sem usar float."""
    try:
        text = str(value or "").strip().replace("R$", "").replace(" ", "")
        if not text:
            return None
        if "," in text and "." not in text:
            text = text.replace(",", ".")
        d = Decimal(text)
        cents = d * 100
        return int(cents) if d > 0 and cents == cents.to_integral_value() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _decode_feed_bytes(raw: bytes) -> str:
    """Aceita CSV puro, gzip ou zip e devolve texto UTF-8."""
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    elif raw[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = [n for n in archive.namelist() if not n.endswith("/")]
            csv_members = [
                n for n in members if n.lower().endswith((".csv", ".txt"))
            ]
            if not csv_members:
                raise ValueError("ZIP não contém arquivo CSV/TXT.")
            raw = archive.read(csv_members[0])

    head = raw[:500].lstrip().lower()
    if head.startswith(b"<!doctype html") or head.startswith(b"<html"):
        raise ValueError(
            "Awin retornou HTML em vez de CSV. Confira a URL/API key do feed."
        )
    return raw.decode("utf-8-sig", errors="replace")


def _sniff_delimiter(text: str) -> str:
    """Detecta vírgula, ponto e vírgula, pipe ou TAB."""
    override = os.getenv("KABUM_FEED_DELIMITADOR", "").strip()
    if override:
        aliases = {"TAB": "\t", "tab": "\t", "\\t": "\t"}
        override = aliases.get(override, override)
        if override not in (",", ";", "|", "\t"):
            raise ValueError(
                "KABUM_FEED_DELIMITADOR deve ser ',', ';', '|', TAB ou ficar vazio."
            )
        return override

    sample = text[:65536]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;|\t")
        return dialect.delimiter
    except csv.Error:
        first = sample.splitlines()[0] if sample.splitlines() else ""
        counts = {d: first.count(d) for d in (",", ";", "|", "\t")}
        return max(counts, key=counts.get)


def _csv_reader(raw: bytes):
    text = _decode_feed_bytes(raw)
    delimiter = _sniff_delimiter(text)
    stream = io.StringIO(text, newline="")
    reader = csv.DictReader(stream, delimiter=delimiter)
    return reader, delimiter


def _fields(reader):
    return {str(x or "").replace("\ufeff", "").strip() for x in (reader.fieldnames or [])}


def is_product_feed(raw: bytes) -> bool:
    reader, _ = _csv_reader(raw)
    return PRODUCT_REQUIRED.issubset(_fields(reader))


def _normalized_row(row):
    return {norm(k): (v or "").strip() for k, v in row.items() if k is not None}


def _pick(row, *aliases):
    for alias in aliases:
        value = row.get(norm(alias), "")
        if value:
            return value
    return ""


def find_product_url_in_feed_list(raw: bytes):
    """Localiza o Product Feed KaBuM dentro do CSV 'Product Feed List' da Awin."""
    advertiser_id = os.getenv("KABUM_AWIN_ADVERTISER_ID", "17729").strip()
    feed_id = os.getenv("KABUM_AWIN_FEED_ID", "46967").strip()
    expected_language = norm(os.getenv("KABUM_AWIN_LANGUAGE", "pt_BR"))

    reader, _ = _csv_reader(raw)
    fields_norm = {norm(x) for x in (reader.fieldnames or [])}

    has_url = bool(
        fields_norm.intersection(
            {"url", "download url", "feed url", "datafeed url", "data feed url"}
        )
    )
    has_feed_identity = bool(
        fields_norm.intersection(
            {"feed id", "datafeed id", "data feed id", "advertiser id", "merchant id"}
        )
    )
    if not (has_url and has_feed_identity):
        return None

    rows = []
    for original in reader:
        row = _normalized_row(original)
        url = _pick(
            row,
            "URL",
            "Download URL",
            "Feed URL",
            "Datafeed URL",
            "Data Feed URL",
        )
        if not url.startswith(("http://", "https://")):
            continue

        adv = _pick(row, "Advertiser ID", "Merchant ID")
        fid = _pick(row, "Feed ID", "Datafeed ID", "Data Feed ID")
        adv_name = _pick(row, "Advertiser Name", "Merchant Name")
        feed_name = _pick(row, "Feed Name", "Datafeed Name", "Data Feed Name")
        language = _pick(row, "Language", "Idioma")
        last_imported = _pick(row, "Last Imported", "Last Updated")

        score = 0
        if feed_id and fid == feed_id:
            score += 100
        if advertiser_id and adv == advertiser_id:
            score += 30
        if "kabum" in norm(adv_name):
            score += 20
        if "kabum" in norm(feed_name):
            score += 10

        lang_norm = norm(language)
        if expected_language and (
            expected_language in lang_norm
            or "portugu" in lang_norm
            or lang_norm in {"pt", "pt_br", "pt-br"}
        ):
            score += 3

        rows.append(
            {
                "score": score,
                "url": url,
                "advertiser_id": adv,
                "feed_id": fid,
                "advertiser_name": adv_name,
                "feed_name": feed_name,
                "language": language,
                "last_imported": last_imported,
            }
        )

    if not rows:
        raise ValueError("A lista de feeds da Awin não contém URLs de download utilizáveis.")

    # Se o ID configurado existir na lista, ele tem prioridade absoluta.
    exact_feed = [r for r in rows if feed_id and r["feed_id"] == feed_id]
    if exact_feed:
        chosen = max(exact_feed, key=lambda x: x["score"])
    else:
        exact_adv = [r for r in rows if advertiser_id and r["advertiser_id"] == advertiser_id]
        if exact_adv:
            chosen = max(exact_adv, key=lambda x: x["score"])
        else:
            kabum = [
                r for r in rows
                if "kabum" in norm(r["advertiser_name"])
                or "kabum" in norm(r["feed_name"])
            ]
            if kabum:
                chosen = max(kabum, key=lambda x: x["score"])
            else:
                raise ValueError(
                    "Lista de feeds reconhecida, mas o feed KaBuM não foi localizado. "
                    "Confira KABUM_AWIN_ADVERTISER_ID e KABUM_AWIN_FEED_ID."
                )

    return chosen


def _read_local_bytes(path: Path) -> bytes:
    if not path.is_file():
        raise FileNotFoundError(f"Feed não encontrado: {path}")
    return path.read_bytes()


def fetch_url(url: str, timeout: int = 60, attempts: int = 3) -> bytes:
    if not url.lower().startswith(("https://", "http://")):
        raise ValueError("A URL da Awin deve começar com http:// ou https://")

    last_error = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()

            if len(raw) < 100:
                raise ValueError("Download retornou conteúdo muito pequeno.")

            # Valida compressão/encoding e recusa HTML.
            _decode_feed_bytes(raw)
            return raw
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(min(2 ** (attempt - 1), 5))

    raise RuntimeError(
        f"Falha ao baixar dados da Awin após {attempts} tentativa(s): {last_error}"
    )


def save_cache(raw: bytes, cache_path: Path):
    """Só salva cache quando o conteúdo é um Product Feed verdadeiro."""
    if not is_product_feed(raw):
        raise ValueError("Tentativa de salvar cache que não é Product Feed.")
    tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
    tmp.write_bytes(raw)
    tmp.replace(cache_path)


def resolve_downloaded_raw(raw: bytes, cache_path: Path, timeout: int, attempts: int):
    """Converte Product Feed List -> Product Feed quando necessário."""
    if is_product_feed(raw):
        save_cache(raw, cache_path)
        return raw, f"Awin Product Feed direto (cache: {cache_path.name})"

    selected = find_product_url_in_feed_list(raw)
    if selected:
        print(
            "Lista de feeds Awin reconhecida. "
            f"Selecionado: {selected['advertiser_name'] or 'KaBuM'} "
            f"| Feed ID {selected['feed_id'] or '?'} "
            f"| {selected['language'] or 'idioma não informado'}"
        )
        product_raw = fetch_url(selected["url"], timeout=timeout, attempts=attempts)
        if not is_product_feed(product_raw):
            reader, delimiter = _csv_reader(product_raw)
            header = list(reader.fieldnames or [])[:12]
            raise ValueError(
                "A URL encontrada na lista não retornou um Product Feed compatível. "
                f"Delimitador detectado={repr(delimiter)}; cabeçalho={header}"
            )
        save_cache(product_raw, cache_path)
        name = selected["feed_name"] or "KaBuM BR Datafeed"
        return (
            product_raw,
            f"Lista Awin -> {name} (Feed ID {selected['feed_id'] or '?'}) "
            f"(cache: {cache_path.name})",
        )

    reader, delimiter = _csv_reader(raw)
    header = list(reader.fieldnames or [])[:12]
    raise ValueError(
        "O arquivo baixado não é Product Feed nem uma Product Feed List reconhecida. "
        f"Delimitador detectado={repr(delimiter)}; cabeçalho={header}"
    )


def find_local_feed(base: Path) -> Path | None:
    candidates = []
    for pattern in (
        "*Kabum*Datafeed*.csv.gz",
        "*Kabum*Datafeed*.csv",
        "*Kabum*Datafeed*.zip",
    ):
        candidates.extend(base.glob(pattern))
    candidates = [p for p in candidates if p.is_file()]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def valid_cached_product(cache_path: Path):
    if not cache_path.is_file():
        return None
    try:
        raw = cache_path.read_bytes()
        return raw if is_product_feed(raw) else None
    except Exception:
        return None


def resolve_feed(args, base: Path):
    """Prioridade: --arquivo > --url > .env > feed local > cache válido."""
    cache_name = os.getenv("KABUM_FEED_CACHE", DEFAULT_CACHE).strip() or DEFAULT_CACHE
    cache_path = base / cache_name
    timeout = int(os.getenv("KABUM_DOWNLOAD_TIMEOUT", "60") or 60)
    attempts = int(os.getenv("KABUM_DOWNLOAD_TENTATIVAS", "3") or 3)

    if args.arquivo:
        path = Path(args.arquivo).expanduser()
        if not path.is_absolute():
            path = base / path
        raw = _read_local_bytes(path)
        if is_product_feed(raw):
            return raw, f"arquivo local: {path.name}"
        return resolve_downloaded_raw(raw, cache_path, timeout, attempts)

    url = args.url or os.getenv("KABUM_AWIN_FEED_URL", "").strip()
    if url:
        try:
            first_raw = fetch_url(url, timeout=timeout, attempts=attempts)
            return resolve_downloaded_raw(first_raw, cache_path, timeout, attempts)
        except Exception as exc:
            cached = valid_cached_product(cache_path)
            if cached is not None and not args.sem_fallback:
                print(f"Aviso: {exc}")
                print(f"Usando cache Product Feed válido: {cache_path.name}")
                return cached, f"cache local: {cache_path.name}"

            local = find_local_feed(base)
            if local and not args.sem_fallback:
                local_raw = local.read_bytes()
                if is_product_feed(local_raw):
                    print(f"Aviso: {exc}")
                    print(f"Usando feed manual existente: {local.name}")
                    return local_raw, f"feed local: {local.name}"
            raise

    local = find_local_feed(base)
    if local:
        raw = local.read_bytes()
        if is_product_feed(raw):
            return raw, f"feed local detectado: {local.name}"

    cached = valid_cached_product(cache_path)
    if cached is not None:
        return cached, f"cache local: {cache_path.name}"

    raise FileNotFoundError(
        "Nenhum Product Feed disponível. Configure KABUM_AWIN_FEED_URL no .env "
        "ou informe --arquivo CAMINHO_DO_FEED.csv.gz."
    )


def init_db(path: Path):
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS kabum_products(
          product_id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          brand TEXT,
          category TEXT,
          price_cents INTEGER NOT NULL,
          affiliate_url TEXT NOT NULL,
          image_url TEXT,
          in_stock TEXT,
          last_seen REAL NOT NULL,
          payload TEXT
        );

        CREATE TABLE IF NOT EXISTS kabum_price_history(
          id INTEGER PRIMARY KEY,
          product_id TEXT NOT NULL,
          price_cents INTEGER NOT NULL,
          observed REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS kabum_hist_idx
          ON kabum_price_history(product_id, observed);
        """
    )
    return db


def ingest(raw: bytes, db, return_changed_ids=False):
    now = time.time()
    total = valid = changes = new_products = 0
    changed_ids = []
    reader, delimiter = _csv_reader(raw)

    fields = _fields(reader)
    if not PRODUCT_REQUIRED.issubset(fields):
        missing = PRODUCT_REQUIRED - fields
        raise ValueError(
            "Product Feed sem colunas obrigatórias: "
            + ", ".join(sorted(missing))
            + f". Delimitador detectado={repr(delimiter)}."
        )

    with db:
        for row in reader:
            total += 1
            pid = (row.get("merchant_product_id") or row.get("aw_product_id") or "").strip()
            price = money(row.get("search_price"))
            link = (row.get("aw_deep_link") or "").strip()
            name = (row.get("product_name") or "").strip()

            if not pid or not name or not price or not link:
                continue

            valid += 1
            old = db.execute(
                "SELECT price_cents FROM kabum_products WHERE product_id=?", (pid,)
            ).fetchone()

            if old is None:
                new_products += 1
                db.execute(
                    "INSERT INTO kabum_price_history(product_id,price_cents,observed) "
                    "VALUES(?,?,?)",
                    (pid, price, now),
                )
            elif old[0] != price:
                changes += 1
                changed_ids.append(pid)
                db.execute(
                    "INSERT INTO kabum_price_history(product_id,price_cents,observed) "
                    "VALUES(?,?,?)",
                    (pid, price, now),
                )

            db.execute(
                """INSERT INTO kabum_products VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(product_id) DO UPDATE SET
                  name=excluded.name,
                  brand=excluded.brand,
                  category=excluded.category,
                  price_cents=excluded.price_cents,
                  affiliate_url=excluded.affiliate_url,
                  image_url=excluded.image_url,
                  in_stock=excluded.in_stock,
                  last_seen=excluded.last_seen,
                  payload=excluded.payload""",
                (
                    pid,
                    name,
                    (row.get("brand_name") or "").strip(),
                    (
                        row.get("merchant_product_category_path")
                        or row.get("merchant_category")
                        or ""
                    ).strip(),
                    price,
                    link,
                    (
                        row.get("large_image")
                        or row.get("merchant_image_url")
                        or row.get("aw_image_url")
                        or ""
                    ).strip(),
                    (row.get("in_stock") or row.get("stock_status") or "").strip(),
                    now,
                    json.dumps(row, ensure_ascii=False),
                ),
            )

    result = (total, valid, new_products, changes)
    return (*result, changed_ids) if return_changed_ids else result


def drops(db, minimum_pct=5, limit=20):
    """Lista apenas quedas comprovadas por observação anterior do próprio histórico."""
    rows = db.execute(
        """SELECT
             p.product_id,p.name,p.brand,p.price_cents,p.affiliate_url,p.image_url,
             (SELECT h.price_cents
                FROM kabum_price_history h
               WHERE h.product_id=p.product_id
                 AND h.price_cents>p.price_cents
               ORDER BY h.observed DESC
               LIMIT 1) previous
           FROM kabum_products p
          ORDER BY p.last_seen DESC"""
    ).fetchall()

    out = []
    for row in rows:
        previous = row[6]
        if previous:
            pct = (previous - row[3]) * 100 / previous
            if pct >= minimum_pct:
                out.append((*row, pct))
    return sorted(out, key=lambda item: item[-1], reverse=True)[:limit]


def brl(cents):
    return (
        f"R$ {cents / 100:,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )



def history_window_days(db, product_id, current_cents, now=None):
    """Maior janela 15..180 dias em que o preço atual é o menor comprovado."""
    now = time.time() if now is None else now
    first = db.execute(
        "SELECT MIN(observed) FROM kabum_price_history WHERE product_id=?",
        (product_id,),
    ).fetchone()[0]
    if first is None:
        return 0

    for days in reversed(HISTORY_WINDOWS):
        start = now - days * DAY
        if first > start:
            continue
        at_start = db.execute(
            """SELECT price_cents FROM kabum_price_history
               WHERE product_id=? AND observed<=?
               ORDER BY observed DESC LIMIT 1""",
            (product_id, start),
        ).fetchone()
        if not at_start:
            continue
        prices = [at_start[0]]
        prices.extend(
            row[0] for row in db.execute(
                """SELECT price_cents FROM kabum_price_history
                   WHERE product_id=? AND observed>? AND observed<=?""",
                (product_id, start, now),
            )
        )
        if prices and current_cents <= min(prices):
            return days
    return 0


def _category_priority(name, category):
    category_text = norm(category)
    if any(term in category_text for term in LOW_PRIORITY_CATEGORY_TERMS):
        return False
    text = norm(f"{category} {name}")
    return any(term in text for term in PRIORITY_CATEGORY_TERMS)


def _brand_priority(name, brand):
    text = norm(f"{brand} {name}")
    return any(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text)
               for term in PRIORITY_BRANDS)


def production_candidates(db, minimum_pct=5, limit=20, max_age_hours=24,
                          availability=None, official_offers=None, now=None):
    """Monta ofertas reais apenas de quedas recentes e comprovadas."""
    from awin_kabum import stock_allows_publication, stock_confirmed

    now = time.time() if now is None else now
    availability = availability or {}
    official_offers = official_offers or {}
    cutoff = now - max(1, float(max_age_hours)) * 3600
    rows = db.execute(
        """SELECT product_id,name,brand,category,price_cents,affiliate_url,
                  image_url,in_stock,last_seen
           FROM kabum_products"""
    ).fetchall()
    out = []
    stamp = datetime.now(timezone.utc).isoformat()

    for pid, name, brand, category, current, affiliate, image, legacy_stock, last_seen in rows:
        latest_current = db.execute(
            """SELECT MAX(observed) FROM kabum_price_history
               WHERE product_id=? AND price_cents=?""",
            (pid, current),
        ).fetchone()[0]
        if latest_current is None or latest_current < cutoff:
            continue
        previous = db.execute(
            """SELECT price_cents FROM kabum_price_history
               WHERE product_id=? AND price_cents<>?
               ORDER BY observed DESC LIMIT 1""",
            (pid, current),
        ).fetchone()
        if not previous or previous[0] <= current:
            continue
        pct = (previous[0] - current) * 100 / previous[0]
        if pct < minimum_pct:
            continue

        stock = availability.get(pid) or legacy_stock or ""
        if not stock_allows_publication(stock):
            continue

        history_days = history_window_days(db, pid, current, now)
        priority_brand = _brand_priority(name, brand)
        priority_category = _category_priority(name, category)
        product_offers = official_offers.get(f"KaBuM:{pid}", [])
        official = product_offers[0] if product_offers else {}
        coupon_code = next(
            (str(item.get("coupon") or "").strip()
             for item in product_offers if str(item.get("coupon") or "").strip()),
            "",
        )
        score = (
            min(pct, 40) * 1.5
            + (history_days / 15) * 2
            + 35 * priority_brand
            + 25 * priority_category
            + 15 * bool(official)
            + 5 * stock_confirmed(stock)
        )
        badge = (
            f"📉 Menor preço observado nos últimos {history_days} dias."
            if history_days
            else f"📉 Queda de {pct:.1f}% confirmada no histórico KaBuM/Awin."
        )
        offer = {
            "product_id": f"KaBuM:{pid}",
            "store": "KaBuM",
            "url": f"https://www.kabum.com.br/produto/{pid}",
            "source": "kabum_feed",
            "kind": "product_offer",
            "name": name,
            "brand": brand,
            "category": category,
            "price": brl(current).replace("R$ ", ""),
            "price_condition": "",
            "price_from": False,
            "coupon": coupon_code,
            "api_image": image if str(image).startswith("https://") else "",
            "source_date": stamp,
            "captured_at": stamp,
            "discount": round(pct, 2),
            "kabum_drop_pct": round(pct, 2),
            "kabum_previous_price_cents": previous[0],
            "kabum_history_days": history_days,
            "history_badge": badge,
            "kabum_brand_priority": priority_brand,
            "kabum_category_priority": priority_category,
            "official_promotion": bool(official),
            "official_offer_title": str(official.get("title") or "").strip(),
            "stock_status": stock,
            "stock_confirmed": stock_confirmed(stock),
            "radar_score": round(score, 3),
            "variant_id": pid,
            "variant_verified": True,
        }
        out.append(offer)

    out.sort(key=lambda item: (-item["radar_score"], item["product_id"]))
    return out[:max(1, int(limit))]


def production_round(base=None, channel="", minimum_pct=None, limit=None, max_age_hours=None):
    """Atualiza feed e entrega candidatos KaBuM ao publicador unificado."""
    from types import SimpleNamespace
    from awin_kabum import AwinKabumAPI
    from ofertas_core import Ledger
    from inteligencia_ofertas import Intelligence

    base = Path(base or Path(__file__).resolve().parent)
    try:
        from dotenv import load_dotenv
        load_dotenv(base / ".env", encoding="utf-8-sig")
    except ImportError:
        pass
    args = SimpleNamespace(arquivo=None, url=None, sem_fallback=False)
    raw, source_description = resolve_feed(args, base)
    db = init_db(base / DB_NAME)
    try:
        total, valid, new_products, changes, changed_ids = ingest(
            raw, db, return_changed_ids=True
        )
        api = AwinKabumAPI.from_env()
        availability = {}
        official = {}
        api_notes = []
        if api.enabled:
            try:
                availability = api.enhanced_availability()
                api_notes.append(f"estoque Enhanced: {len(availability)} produtos")
            except Exception as exc:
                api_notes.append(f"estoque Enhanced indisponível ({type(exc).__name__})")
            try:
                official = api.product_offer_map()
                api_notes.append(f"Offers oficiais: {sum(map(len, official.values()))}")
            except Exception as exc:
                api_notes.append(f"Offers API indisponível ({type(exc).__name__})")
        else:
            api_notes.append("APIs Awin opcionais aguardando credenciais")

        minimum_pct = (
            float(os.getenv("KABUM_MIN_QUEDA_PCT", "5") or 5)
            if minimum_pct is None else float(minimum_pct)
        )
        limit = (
            max(1, int(os.getenv("KABUM_QUEUE_LIMIT", "20") or 20))
            if limit is None else max(1, int(limit))
        )
        max_age = (
            max(1, float(os.getenv("KABUM_CANDIDATE_MAX_AGE_HOURS", "24") or 24))
            if max_age_hours is None else max(1, float(max_age_hours))
        )
        candidates = production_candidates(
            db, minimum_pct=minimum_pct, limit=limit, max_age_hours=max_age,
            availability=availability, official_offers=official,
        )
    finally:
        db.close()

    ledger = Ledger(base / "publicacoes.sqlite3")
    try:
        intelligence = Intelligence(ledger.db)
        intelligence.enqueue(candidates)
        kabum_pending = [
            offer for offer in intelligence.pending(channel)
            if offer.get("source") == "kabum_feed"
        ]
    finally:
        ledger.db.close()

    return {
        "source": source_description,
        "total": total,
        "valid": valid,
        "new_products": new_products,
        "changes": changes,
        "changed_ids": changed_ids,
        "candidates": candidates,
        "pending": len(kabum_pending),
        "api_notes": api_notes,
    }


def main():
    base = Path(__file__).resolve().parent
    try:
        from dotenv import load_dotenv
        load_dotenv(base / ".env", encoding="utf-8-sig")
    except ImportError:
        pass

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arquivo", help="CSV, CSV.GZ ou ZIP local da Awin.")
    parser.add_argument(
        "--url",
        help="URL privada Awin (Product Feed ou Product Feed List). "
             "Prefira KABUM_AWIN_FEED_URL no .env.",
    )
    parser.add_argument("--queda-min", type=float, default=None)
    parser.add_argument("--limite", type=int, default=20)
    parser.add_argument(
        "--enfileirar", action="store_true",
        help="Atualiza o feed e entrega candidatas ao publicador unificado real."
    )
    parser.add_argument(
        "--sem-fallback",
        action="store_true",
        help="Se o download falhar, não usa cache/feed local.",
    )
    args = parser.parse_args()

    minimum_pct = args.queda_min
    if minimum_pct is None:
        minimum_pct = float(os.getenv("KABUM_MIN_QUEDA_PCT", "5") or 5)

    if minimum_pct < 0 or minimum_pct > 100:
        parser.error("--queda-min deve ficar entre 0 e 100.")
    if args.limite < 1:
        parser.error("--limite deve ser pelo menos 1.")

    if args.enfileirar:
        try:
            result = production_round(
                base, os.getenv("TELEGRAM_CANAL", ""),
                minimum_pct=minimum_pct, limit=args.limite,
            )
        except Exception as exc:
            parser.exit(1, f"Erro na rodada KaBuM de produção: {exc}\n")
        print(
            f"KaBuM produção: {result['valid']} produtos; "
            f"{result['changes']} preços alterados; "
            f"{len(result['candidates'])} candidatas; "
            f"{result['pending']} na fila."
        )
        for note in result["api_notes"]:
            print("-", note)
        return

    try:
        raw, source_description = resolve_feed(args, base)
    except Exception as exc:
        parser.exit(1, f"Erro ao obter feed KaBuM/Awin: {exc}\n")

    db = init_db(base / DB_NAME)
    try:
        total, valid, new_products, changes = ingest(raw, db)
        print(f"Fonte: {source_description}")
        print(
            f"KaBuM/Awin: {total} linhas lidas; "
            f"{valid} produtos válidos registrados."
        )
        print(
            f"Novos produtos nesta coleta: {new_products} | "
            f"preços alterados: {changes}"
        )

        detected = drops(db, minimum_pct, args.limite)
        if not detected:
            print(
                "Nenhuma queda comprovada no histórico "
                "com o limite configurado."
            )
        else:
            print(f"Quedas comprovadas >= {minimum_pct:g}%:")
            for row in detected:
                print(
                    f"- {row[1]} | {brl(row[3])} | "
                    f"antes {brl(row[6])} | queda {row[7]:.1f}%"
                )

        print("Modo diagnóstico: nenhuma publicação foi enviada.")
    finally:
        db.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrompido pelo usuário.")