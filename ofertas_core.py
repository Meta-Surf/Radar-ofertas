"""Funções locais: produtos, cupons e registro persistente por dia."""
import html
import json
from pathlib import Path
import re
import sqlite3
import time
from datetime import datetime
from urllib.parse import urlparse, parse_qs
from zoneinfo import ZoneInfo
from html.parser import HTMLParser
from urllib.parse import urljoin

HOSTS = {'shopee.com.br', 'www.shopee.com.br', 's.shopee.com.br', 'shope.ee',
         'amazon.com.br', 'www.amazon.com.br', 'amzn.to',
         'mercadolivre.com.br', 'www.mercadolivre.com.br', 'produto.mercadolivre.com.br',
         'mercadolivre.com', 'www.mercadolivre.com', 'meli.la',
         'kabum.com.br', 'www.kabum.com.br'}

# Intermediário usado nas ofertas enviadas pelo usuário; não é uma loja.
PRODUCT_REDIRECTORS = {'desconto.games'}

# Correspondências informadas pelo usuário. Não são links de afiliado.
# Só o endereço exato (ignorando fragmento e barra final) recebe este destino.
KNOWN_DESTINATIONS = {
    'https://meli.la/2PnnX9t': (
        'https://www.mercadolivre.com.br/'
        'placa-de-video-msi-nvidia-rtx-5060-shadow-2x-oc-8gb-gddr7/'
        'up/MLBU3669698149?pdp_filters=item_id%3AMLB4360643061'
    ),
}

def safe_url(url):
    try:
        p = urlparse(url)
        return (p.scheme == 'https' and p.hostname in HOSTS | PRODUCT_REDIRECTORS
                and not p.username and not p.password and p.port in (None, 443))
    except (ValueError, TypeError):
        return False

def product(url):
    if not safe_url(url):
        return None
    p = urlparse(url)
    if p.hostname.endswith('shopee.com.br'):
        m = re.search(r'(?:/product/|/i\.)(\d+)[/.](\d+)', p.path)
        if not m:
            m = re.fullmatch(r'/opaanlp/(\d+)/(\d+)/?', p.path)
        if not m:
            m = re.search(r'-i\.(\d+)\.(\d+)', p.path)
        if m:
            shop, item = m.groups()
            return ('Shopee:' + shop + ':' + item, 'Shopee', f'https://shopee.com.br/product/{shop}/{item}')
    if p.hostname in ('kabum.com.br', 'www.kabum.com.br'):
        m = re.match(r'^/produto/(\d+)(?:/|$)', p.path, re.I)
        if m:
            item = m[1]
            return ('KaBuM:' + item, 'KaBuM',
                    'https://www.kabum.com.br/produto/' + item)
    if p.hostname.endswith('amazon.com.br'):
        m = re.search(r'/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})(?:/|$)', p.path, re.I)
        if m:
            asin = m[1].upper()
            return ('Amazon:' + asin, 'Amazon', 'https://www.amazon.com.br/dp/' + asin)
    if 'mercadolivre' in p.hostname:
        query = parse_qs(p.query)

        def exact_item(values):
            for value in values:
                match = re.fullmatch(r'\s*MLB-?(\d+)\s*', str(value), re.I)
                if match:
                    return match[1]
            return None

        # Só aceita IDs de item explicitamente rotulados. Um pdp_filters como
        # "deal:MLB779362-1" identifica a campanha, não o produto.
        item = exact_item(query.get('item_id', []))
        if not item:
            for value in query.get('pdp_filters', []):
                match = re.search(r'(?:^|[,;|\s])item_id\s*:\s*MLB-?(\d+)(?=$|[,;|\s])',
                                  str(value), re.I)
                if match:
                    item = match[1]
                    break

        # Em páginas de catálogo /p/, wid aponta para o anúncio/vendedor efetivo.
        if not item:
            item = exact_item(query.get('wid', []))

        if item:
            url = 'https://produto.mercadolivre.com.br/MLB-' + item + '-_JM'
            return ('MercadoLivre:' + item, 'Mercado Livre', url)

        catalog = re.search(r'/p/MLB-?(\d+)(?:/|$)', p.path, re.I)
        if catalog:
            item = catalog[1]
            return ('MercadoLivre:' + item, 'Mercado Livre',
                    'https://www.mercadolivre.com.br/p/MLB' + item)

        listing = re.search(r'MLB-?(\d+)', p.path, re.I)
        if listing:
            item = listing[1]
            return ('MercadoLivre:' + item, 'Mercado Livre',
                    'https://produto.mercadolivre.com.br/MLB-' + item + '-_JM')
    return None

def embedded_product(url):
    """Reconhece destino explícito em URL da loja, sem executar JavaScript."""
    candidates = [url]
    visited = set()
    for _ in range(4):
        next_candidates = []
        for candidate in candidates:
            if candidate in visited or not safe_url(candidate):
                continue
            visited.add(candidate)
            found = product(candidate)
            if found:
                return found
            parsed = urlparse(candidate)
            if parsed.hostname not in ('shopee.com.br', 'www.shopee.com.br'):
                continue
            args = parse_qs(parsed.query)
            # Pares explícitos; nunca combina IDs encontrados em blocos diferentes.
            shop = args.get('shopid', args.get('shop_id', ['']))[0]
            item = args.get('itemid', args.get('item_id', ['']))[0]
            if shop.isdigit() and item.isdigit():
                return product(f'https://shopee.com.br/product/{shop}/{item}')
            for name in ('url', 'target', 'target_url', 'redirect', 'redirect_url', 'originUrl'):
                next_candidates.extend(urljoin(candidate, v) for v in args.get(name, []))
        candidates = next_candidates
    return None

class PageDestinations(HTMLParser):
    """Lê apenas o destino principal; não varre links de produtos recomendados."""
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'link' and 'canonical' in (attrs.get('rel') or '').lower().split():
            self.urls.append(attrs.get('href') or '')
        elif tag == 'meta':
            label = (attrs.get('property') or attrs.get('name') or '').lower()
            if label in ('og:url', 'al:web:url'):
                self.urls.append(attrs.get('content') or '')
            elif (attrs.get('http-equiv') or '').lower() == 'refresh':
                m = re.search(r'url\s*=\s*(.+)', attrs.get('content') or '', re.I)
                if m:
                    self.urls.append(m[1].strip().strip('\"\''))

def page_product(content, base_url):
    parser = PageDestinations()
    parser.feed(content)
    found = {}
    for candidate in parser.urls:
        result = embedded_product(urljoin(base_url, candidate))
        if result and urlparse(result[2]).hostname in ('shopee.com.br', 'www.shopee.com.br'):
            found[result[0]] = result
    return next(iter(found.values())) if len(found) == 1 else None

def resolve(url, report=None):
    """Resolve HTTP e metadados principais da Shopee; nunca executa scripts."""
    report = report or (lambda text: None)
    key = url.split('#', 1)[0].rstrip('/')
    confirmed = KNOWN_DESTINATIONS.get(key)
    if safe_url(url) and urlparse(url).hostname in PRODUCT_REDIRECTORS:
        mapping_path = Path(__file__).resolve().parent / 'destinos_confirmados.json'
        if mapping_path.exists():
            try:
                mappings = json.loads(mapping_path.read_text(encoding='utf-8'))
                candidate = mappings.get(key) if isinstance(mappings, dict) else None
                identified = product(candidate) if isinstance(candidate, str) else None
                if identified and identified[1] == 'Shopee':
                    confirmed = identified[2]
            except (OSError, ValueError):
                report('Cadastro local de destinos inválido; tentando resolução normal.')
    if confirmed:
        known = product(confirmed)
        if known:
            report('Destino informado pelo usuário: link reconhecido sem consultar o encurtador.')
            return known
    import requests
    visited = set()
    for _ in range(6):
        if not safe_url(url):
            report('Redirecionamento para domínio não reconhecido.')
            return None
        if url in visited:
            report('Redirecionamento circular; produto não identificado.')
            return None
        visited.add(url)
        known = embedded_product(url)
        if known:
            return known
        with requests.get(url, allow_redirects=False, stream=True, timeout=15) as r:
            report(f'HTTP {r.status_code} em {urlparse(url).hostname}')
            if r.status_code not in (301, 302, 303, 307, 308):
                if r.status_code == 200 and urlparse(url).hostname in ('shopee.com.br', 'www.shopee.com.br'):
                    report('Caminho final: ' + urlparse(url).path[:180])
                    report('Parâmetros presentes: ' + ', '.join(parse_qs(urlparse(url).query).keys()))
                    data = bytearray()
                    for chunk in r.iter_content(16384):
                        data.extend(chunk)
                        if len(data) >= 524288:
                            break
                    found = page_product(bytes(data[:524288]).decode('utf-8', errors='replace'), url)
                    if found:
                        report('Produto encontrado nos metadados principais da página.')
                        return found
                    report('Metadados principais não identificaram um produto único.')
                report('Página sem redirecionamento HTTP; produto não identificado.')
                return None
            location = r.headers.get('Location')
            if not location:
                report('Redirecionamento sem cabeçalho Location; produto não identificado.')
                return None
            url = urljoin(url, location)
    report('Limite de redirecionamentos atingido.')
    return None

def coupon_page_links(text):
    """Mantém o rótulo do cupom através de condições até o próximo link."""
    result = set()
    coupon_block = False
    for line in text.splitlines():
        if not line.strip():
            continue
        urls = re.findall(r'https?://[^\s<>]+', line)
        context = re.sub(r'https?://[^\s<>]+', '', line).strip()
        # Setas, marcadores e Markdown antes da URL não são um novo rótulo.
        context = re.sub(r'^[^\w]+', '', context).strip()
        if re.search(r'\b(?:link|p[aá]gina)\s+(?:do\s+)?produto\b|\b(?:comprar|compre)\b', context, re.I):
            coupon_block = False
        elif (re.search(r'\b(?:cupom|cupons)\b', context, re.I)
              and re.search(r'resgat|p[aá]gina', context, re.I)):
            coupon_block = True
        elif context and not re.search(
                r'R\$|\d\s*%|\b(?:OFF|selecionad\w*|categorias|acima|m[ií]nimo|v[aá]lid\w*|expira\w*|frete|primeira compra|c[oó]digo|confira|clique|aqui|link)\b',
                context, re.I):
            coupon_block = False
        if urls:
            if coupon_block:
                result.update(u.rstrip('.,;!?)"\']') for u in urls)
            coupon_block = False
    return result

def extract_links(message):
    text = message.raw_text or ''
    links = re.findall(r'https?://[^\s<>]+', text)
    for entity, value in message.get_entities_text():
        if getattr(entity, 'url', None):
            links.append(entity.url)
    markup = getattr(message, 'reply_markup', None)
    for row in getattr(markup, 'rows', []) or []:
        for button in row.buttons:
            if getattr(button, 'url', None):
                links.append(button.url)
    return list(dict.fromkeys(u.rstrip('.,;!?)\"\']') for u in links))

def coupon(text):
    # Só aceita código junto a um rótulo explícito; não inventa validade.
    m = re.search(r'\bcupom\s*(?:de\s+desconto\s*)?[:=\-🎟️\s]*[\"`\[]?([A-Z0-9][A-Z0-9_-]{3,29})\b', text, re.I)
    if not m:
        return None
    value = m[1]
    if value.lower() in {'desconto','disponivel','disponível','aplique','resgate','clique','automatico','automático','loja','vendedor'}:
        return None
    if value.isdigit():
        return None
    return value

def price_info(text):
    """Extrai valor explícito e condição, sem calcular descontos ou parcelas."""
    # Aceita moeda ou emoji monetário explícito; milhares BR e US.
    money = re.compile(r'(?:R\s*\$|[💵💰💸])\s*(?:R\s*\$\s*)?(\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\d{1,3}(?:\.\d{3})+(?:,\d{2})?|\d+(?:[,.]\d{2})?)(?![\d.,])', re.I)
    candidates = []
    clean_text = html.unescape(text).replace('\xa0', ' ').replace('\u200b', '').replace('\ufe0f', '')
    previous = ''
    for raw_line in clean_text.splitlines():
        # Valores riscados representam geralmente o preço anterior.
        line = re.sub(r'~~.*?~~', '', raw_line)
        line = re.sub(r'https?://\S+', '', line)
        line = re.sub(r'[*_`]', '', line).strip()
        previous_line = previous
        if line:
            previous = line
        for match in money.finditer(line):
            before, after = line[:match.start()].strip(), line[match.end():].strip()
            prefix = re.sub(r'^[^\w]+', '', before).strip()
            if re.search(r'\d+\s*(?:x|vezes)\b', prefix, re.I):
                continue
            if not prefix and re.search(r'\b(?:cupom|desconto|frete|cashback|parcela)\s*[:=]?\s*$', previous_line, re.I):
                continue
            # Um preço final pode vir após "De R$ ... por R$ ...".
            explicit_final = bool(re.search(r'\bpor\s*[:=\-]?\s*$', prefix, re.I))
            if explicit_final:
                # "Frete por", "cupom por" etc. não são preço do produto.
                if re.search(r'\b(?:frete|cupom|desconto|cashback|parcela)\b', prefix, re.I):
                    continue
            elif not re.fullmatch(r'(?:(?:preço|valor)(?:\s+(?:final|promocional))?|agora|apenas|s[oó]|somente|a partir de)?\s*[:=\-]?\s*', prefix, re.I):
                continue
            # Exclui valor do desconto, limiar de cupom e mensalidade.
            if re.match(r'(?:OFF\b|de\s+(?:desconto|cashback)\b|/\s*m[eê]s|por\s+m[eê]s|cada\s+parcela)', after, re.I):
                continue
            if re.search(r'\b(?:cupom|cupons|desconto)\b', line, re.I) and re.match(r'(?:em|acima|nas compras)\b', after, re.I):
                continue
            if after and not re.match(r'(?:no\s+(?:app|aplicativo|pix|boleto|cart[aã]o)|via\s+pix|[àa]\s+vista|com\s+(?:o\s+)?cupom|usando\s+(?:o\s+)?cupom|aplicando\s+(?:o\s+)?cupom|em\s+(?:at[eé]\s+)?[1-9]\d?\s*(?:x\b|vezes\b|parcelas\b)(?!\s+(?:de|por)\b)|no\s+pagamento|[!✅🔥💰💵💸🎉])', after, re.I):
                continue
            value = match[1]
            if re.fullmatch(r'\d{1,3}(?:,\d{3})+(?:\.\d{2})?', value):
                whole, _, cents = value.replace(',', '').partition('.')
                cents = cents or '00'
            elif ',' in value:
                whole, cents = value.replace('.', '').split(',')
            elif re.fullmatch(r'\d+\.\d{2}', value):
                whole, cents = value.split('.')
            else:
                whole, cents = value.replace('.', ''), '00'
            if int(whole) == 0 and int(cents) == 0:
                continue
            formatted = f'{int(whole):,}'.replace(',', '.') + ',' + cents
            condition = after.strip(' !✅🔥💰💵💸🎉')
            candidates.append({'price': formatted, 'price_condition': condition,
                               'price_from': bool(re.search(r'\ba partir de\b', prefix, re.I))})
    # Nunca escolhe o menor de dois preços/modos de pagamento sem contexto.
    identities = {(c['price'], c['price_condition'], c['price_from']) for c in candidates}
    if len(identities) != 1:
        return None
    info = candidates[0]
    # Preserva uma instrução explícita de ativação; não subtrai a porcentagem.
    activation = re.compile(
        r'(?:ative|aplique|use|utilize)\s+(?:o\s+)?cupom\s+(?:de\s+)?'
        r'\d{1,2}(?:[,.]\d{1,2})?\s*%\s*(?:OFF|de\s+desconto)?'
        r'(?:\s+no\s+(?:carrinho|app|aplicativo))?[.!]?', re.I)
    for raw in clean_text.splitlines():
        line = re.sub(r'[*_`]', '', raw).strip().lstrip('-• ').strip()
        if activation.fullmatch(line) and line.lower() not in info['price_condition'].lower():
            info['price_condition'] = '; '.join(filter(None, [info['price_condition'], line]))
    return info

def price(text):
    info = price_info(text)
    return info['price'] if info else None

def caption(offer):
    # Texto enxuto; não acrescenta características ou condições não confirmadas.
    parts = ['🛍️ <b>' + html.escape(offer['store'].upper()) + '</b>']
    if offer.get('name'):
        name = ' '.join(str(offer['name']).split())
        if len(name) > 160:
            name = name[:157].rsplit(' ', 1)[0] + '…'
        parts.append('<b>' + html.escape(name) + '</b>')
    if offer.get('price'):
        prefix = 'a partir de ' if offer.get('price_from') else ''
        price_line = '💰 <b>' + prefix + 'R$ ' + html.escape(offer['price']) + '</b>'
        parts.append(price_line)
        if offer.get('history_badge'):
            parts.append(html.escape(offer['history_badge']))
        if offer.get('official_offer_title'):
            parts.append('🏷️ Oferta oficial KaBuM/Awin: ' +
                         html.escape(str(offer['official_offer_title'])[:180]))
        if offer.get('stock_confirmed') is True:
            if offer.get('store') == 'KaBuM':
                parts.append('✅ Disponibilidade confirmada no feed Awin.')
            elif offer.get('store') == 'Amazon':
                parts.append('✅ Disponibilidade confirmada pela Amazon Creators API.')
            else:
                parts.append('✅ Disponibilidade confirmada pela fonte oficial.')
        if offer.get('source') == 'shopee_api':
            parts.append('Antes de cupons e descontos de pagamento.')
        elif offer.get('price_condition'):
            parts.append(html.escape(str(offer['price_condition'])))
        if offer.get('source') == 'shopee_api':
            parts.append('🎟️ Confira cupons e possíveis descontos no Pix na página do produto.')
    if offer.get('coupon'):
        parts.append('🎟️ Cupom: <code>' + html.escape(offer['coupon']) + '</code>'
                     + '\nConfira as condições do cupom na loja.')
    parts.append('⚠️ Preço e disponibilidade sujeitos a alteração.')
    if offer.get('affiliate_generated'):
        parts.append('(ANÚNCIO)')
    return '\n\n'.join(parts)

class Ledger:
    RESERVATION_STALE_SECONDS = 600

    def __init__(self, path):
        from distribuicao import DeliveryOutbox
        self.db = sqlite3.connect(path, timeout=30)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS posts (product TEXT, day TEXT, status TEXT, message_id INTEGER, PRIMARY KEY(product, day))')
        columns = {row[1] for row in self.db.execute('PRAGMA table_info(posts)')}
        for name, kind in (
            ('reserved_at', 'REAL'),
            ('send_started_at', 'REAL'),
            ('updated_at', 'REAL'),
        ):
            if name not in columns:
                self.db.execute(f'ALTER TABLE posts ADD COLUMN {name} {kind}')
        self.db.execute('CREATE INDEX IF NOT EXISTS posts_status_idx ON posts(status)')
        self.db.execute('CREATE TABLE IF NOT EXISTS publication_clock (id INTEGER PRIMARY KEY, next_at REAL NOT NULL)')
        self.db.execute('''CREATE TABLE IF NOT EXISTS publication_selections (
          product TEXT NOT NULL, day TEXT NOT NULL, selection TEXT NOT NULL,
          PRIMARY KEY(product,day))''')
        self.deliveries = DeliveryOutbox(self.db)
        self.db.commit()

    def publication_delay(self, clock_id=1):
        row = self.db.execute('SELECT next_at FROM publication_clock WHERE id=?', (clock_id,)).fetchone()
        return max(0, row[0] - time.time()) if row else 0

    def mark_attempt(self, interval, clock_id=1):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO publication_clock VALUES (?, ?)', (clock_id, time.time() + interval))

    def reconcile_reservations(self):
        """Recupera reservas do processo anterior sem arriscar duplicação."""
        now = time.time()
        with self.db:
            uncertain = self.db.execute(
                """UPDATE posts SET status='uncertain', updated_at=?
                   WHERE status='sending'""",
                (now,),
            ).rowcount
            # Em um novo processo protegido por instancia_unica, qualquer estado
            # reserved pertence ao processo anterior e ainda não iniciou o envio.
            released = self.db.execute(
                "DELETE FROM posts WHERE status='reserved'"
            ).rowcount
        pending_uncertain = self.db.execute(
            "SELECT COUNT(*) FROM posts WHERE status='uncertain'"
        ).fetchone()[0]
        return {
            'moved_to_uncertain': uncertain,
            'released_abandoned_reserved': released,
            'uncertain_total': pending_uncertain,
        }

    def reserve(self, product_id, moment=None, offer=None, channel="", *, selected=None):
        from inteligencia_ofertas import Intelligence
        intelligence = Intelligence(self.db)
        day = (moment or datetime.now(ZoneInfo('America/Sao_Paulo'))).astimezone(ZoneInfo('America/Sao_Paulo')).date().isoformat()
        now = time.time()
        cutoff = now - self.RESERVATION_STALE_SECONDS
        # Serializa limpeza, verificação e reserva entre processos concorrentes.
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if selected is not None:
                from revisao_publicacao import matches
                if not matches(self.db, selected, now=now):
                    self.db.rollback()
                    return None
            self.db.execute(
                """DELETE FROM posts
                   WHERE status='reserved'
                     AND reserved_at IS NOT NULL AND reserved_at<?""",
                (cutoff,),
            )
            # O produto é identificado pelo par loja/item. Publicações anteriores
            # só são liberadas com queda comparável após 24h; envios incertos ficam bloqueados
            # até reconciliação explícita para evitar duplicatas.
            if self.db.execute('SELECT 1 FROM posts WHERE product=? LIMIT 1', (product_id,)).fetchone() and (
                    not offer or self.db.execute(
                        "SELECT 1 FROM posts WHERE product=? AND status!='sent'", (product_id,)).fetchone()
                    or not intelligence.can_repeat(offer, channel)):
                self.db.commit()
                return None
            result = self.db.execute(
                """INSERT OR IGNORE INTO posts
                   (product,day,status,message_id,reserved_at,send_started_at,updated_at)
                   VALUES (?,?,?,NULL,?,NULL,?)""",
                (product_id, day, 'reserved', now, now),
            )
            if result.rowcount and selected is not None:
                from revisao_publicacao import selection_id
                self.db.execute('INSERT OR REPLACE INTO publication_selections VALUES (?,?,?)',
                                (product_id, day, selection_id(selected)))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return day if result.rowcount else None

    def mark_sending(self, product_id, day, *, selected=None, catalog_path=None, catalog_proof=None):
        if selected is not None:
            from revisao_publicacao import catalog_guard, immediate, matches, selection_id
            # O commit de reserved -> sending é a fronteira de autorização.
            # O catálogo permanece bloqueado até esse commit, sem escrever nele.
            with catalog_guard(catalog_path, catalog_proof) as source_matches:
                with immediate(self.db):
                    binding = self.db.execute(
                        'SELECT selection FROM publication_selections WHERE product=? AND day=?',
                        (product_id, day)).fetchone()
                    if not binding or binding[0] != selection_id(selected):
                        return False
                    if not source_matches or not matches(self.db, selected):
                        self.db.execute("DELETE FROM posts WHERE product=? AND day=? AND status='reserved'",
                                        (product_id, day))
                        return False
                    return bool(self.db.execute(
                        "UPDATE posts SET status='sending',send_started_at=?,updated_at=? "
                        "WHERE product=? AND day=? AND status='reserved'",
                        (time.time(), time.time(), product_id, day)).rowcount)
        now = time.time()
        with self.db:
            result = self.db.execute(
                """UPDATE posts
                   SET status='sending', send_started_at=?, updated_at=?
                   WHERE product=? AND day=? AND status='reserved'""",
                (now, now, product_id, day),
            )
        return bool(result.rowcount)

    def cancel_reserved_selection(self, product_id, day, selected):
        """Erro pré-envio não libera outra reserva, SENDING, UNCERTAIN ou SENT."""
        from revisao_publicacao import immediate, selection_id
        with immediate(self.db):
            return self.db.execute(
                "DELETE FROM posts WHERE product=? AND day=? AND status='reserved' "
                "AND EXISTS(SELECT 1 FROM publication_selections s WHERE "
                "s.product=posts.product AND s.day=posts.day AND s.selection=?)",
                (product_id,day,selection_id(selected))).rowcount

    def mark_uncertain(self, product_id, day, *, offer=None, channel=""):
        now = time.time()
        with self.db:
            result = self.db.execute(
                """UPDATE posts SET status='uncertain', updated_at=?
                   WHERE product=? AND day=? AND status IN ('reserved','sending')""",
                (now, product_id, day),
            )
            if result.rowcount:
                self.deliveries.record_uncertain(
                    product_id, destination="telegram", account=channel,
                    surface="channel", day=day, payload=offer,
                    reason="ENVIO_INCERTO", commit=False,
                )
        return bool(result.rowcount)

    def finish(self, product_id, day, message_id, offer=None, channel="", *, selected=None):
        from inteligencia_ofertas import Intelligence
        intelligence = Intelligence(self.db)
        from revisao_publicacao import immediate, selection_id, discard_selected
        with immediate(self.db):
            if selected is not None:
                row = self.db.execute(
                    'SELECT p.status,p.message_id,s.selection FROM posts p '
                    'JOIN publication_selections s ON p.product=s.product AND p.day=s.day '
                    'WHERE p.product=? AND p.day=?', (product_id, day)).fetchone()
                if not row or row[2] != selection_id(selected):
                    raise ValueError('Confirmação não corresponde à revisão autorizada')
                if row[0] == 'sent':
                    if str(row[1]) == str(message_id):
                        return
                    raise ValueError('Confirmação SENT possui outro message_id')
                if row[0] not in ('sending', 'uncertain'):
                    raise ValueError('Revisão ainda não autorizada para envio')
            self.db.execute(
                """UPDATE posts SET status=?, message_id=?, updated_at=?
                   WHERE product=? AND day=?""",
                ('sent', message_id, time.time(), product_id, day),
            )
            self.deliveries.record_sent(
                product_id, destination="telegram", account=channel,
                surface="channel", day=day, external_id=message_id,
                payload=offer, commit=False,
            )
            if offer and offer.get('kind') != 'coupon_alert':
                intelligence.record(offer, channel, message_id, selected=selected)
            if selected is not None:
                discard_selected(self.db, selected)

    def release(self, product_id, day):
        """Libera apenas estados cujo não-envio é conhecido; nunca remove uncertain."""
        with self.db:
            self.db.execute(
                """DELETE FROM posts
                   WHERE product=? AND day=? AND status IN ('reserved','sending')""",
                (product_id, day),
            )
