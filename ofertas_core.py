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
         'mercadolivre.com', 'www.mercadolivre.com', 'meli.la'}

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
    if p.hostname.endswith('amazon.com.br'):
        m = re.search(r'/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})(?:/|$)', p.path, re.I)
        if m:
            asin = m[1].upper()
            return ('Amazon:' + asin, 'Amazon', 'https://www.amazon.com.br/dp/' + asin)
    if 'mercadolivre' in p.hostname:
        query = parse_qs(p.query)
        identity = ' '.join(query.get('item_id', []) + query.get('pdp_filters', []))
        m = re.search(r'MLB-?(\d+)', identity, re.I) or re.search(r'MLB-?(\d+)', p.path, re.I)
        if m:
            item = m[1]
            url = ('https://www.mercadolivre.com.br/p/MLB' + item if '/p/' in p.path and not identity
                   else 'https://produto.mercadolivre.com.br/MLB-' + item + '-_JM')
            return ('MercadoLivre:' + item, 'Mercado Livre', url)
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
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=30)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS posts (product TEXT, day TEXT, status TEXT, message_id INTEGER, PRIMARY KEY(product, day))')
        self.db.execute('CREATE TABLE IF NOT EXISTS publication_clock (id INTEGER PRIMARY KEY, next_at REAL NOT NULL)')
        self.db.commit()

    def publication_delay(self, clock_id=1):
        row = self.db.execute('SELECT next_at FROM publication_clock WHERE id=?', (clock_id,)).fetchone()
        return max(0, row[0] - time.time()) if row else 0

    def mark_attempt(self, interval, clock_id=1):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO publication_clock VALUES (?, ?)', (clock_id, time.time() + interval))

    def reserve(self, product_id, moment=None, offer=None, channel=""):
        from inteligencia_ofertas import Intelligence
        intelligence = Intelligence(self.db)
        day = (moment or datetime.now(ZoneInfo('America/Sao_Paulo'))).astimezone(ZoneInfo('America/Sao_Paulo')).date().isoformat()
        # Serializa a verificação e a reserva entre processos concorrentes.
        self.db.execute('BEGIN IMMEDIATE')
        try:
            # O produto é identificado pelo par loja/item. Publicações anteriores
            # só são liberadas com queda comparável após 24h; resultados incertos ficam bloqueados.
            if self.db.execute('SELECT 1 FROM posts WHERE product=? LIMIT 1', (product_id,)).fetchone() and (
                    not offer or self.db.execute(
                        "SELECT 1 FROM posts WHERE product=? AND status!='sent'", (product_id,)).fetchone()
                    or not intelligence.can_repeat(offer, channel)):
                self.db.commit()
                return None
            result = self.db.execute('INSERT OR IGNORE INTO posts VALUES (?, ?, ?, NULL)', (product_id, day, 'sending'))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return day if result.rowcount else None

    def finish(self, product_id, day, message_id, offer=None, channel=""):
        from inteligencia_ofertas import Intelligence
        intelligence = Intelligence(self.db)
        with self.db:
            self.db.execute('UPDATE posts SET status=?, message_id=? WHERE product=? AND day=?', ('sent', message_id, product_id, day))
            if offer and offer.get('kind') != 'coupon_alert':
                intelligence.record(offer, channel, message_id)

    def release(self, product_id, day):
        with self.db:
            self.db.execute('DELETE FROM posts WHERE product=? AND day=? AND status=?', (product_id, day, 'sending'))
