"""Lê produto ML do link informado, sem alterar o link de afiliado ou estimar preço."""
import hashlib
import html
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

import requests
from mercadolivre_manual import allowed_link, trusted, key, pending_key
from ofertas_core import product
from shopee_afiliados import AffiliateError

DEFAULT_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'
)


def reader_cookie():
    value = os.getenv('ML_READER_COOKIE', '').strip()
    if value and '\r' not in value and '\n' not in value and len(value) <= 100000:
        return value
    return ''


def request_headers():
    headers = {
        'User-Agent': os.getenv('ML_READER_USER_AGENT', DEFAULT_USER_AGENT),
        'Accept-Language': 'pt-BR,pt;q=0.9,en;q=0.8',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    }
    cookie = reader_cookie()
    if cookie:
        headers['Cookie'] = cookie
    return headers



class Page(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.documents, self.links, self.canonical = [], [], []
        self.anchors = []
        self.refresh, self._json, self._anchor = [], None, None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'script' and a.get('type', '').split(';')[0] == 'application/ld+json':
            self._json = ''
        if tag == 'a' and a.get('href'):
            self.links.append(a['href'])
            if self._anchor is None:
                self._anchor = {'href': a['href'], 'text': []}
        if tag == 'link' and a.get('rel') == 'canonical' and a.get('href'):
            self.canonical.append(a['href'])
        if tag == 'meta':
            label = (a.get('property') or a.get('name') or '').lower()
            if label in ('og:url', 'al:web:url') and a.get('content'):
                self.canonical.append(a['content'])
            if a.get('http-equiv', '').lower() == 'refresh':
                match = re.search(r'url\s*=\s*[\"\']?([^\"\']+)', a.get('content', ''), re.I)
                if match:
                    self.refresh.append(html.unescape(match[1].strip()))

    def handle_data(self, data):
        if self._json is not None:
            self._json += data
        if self._anchor is not None:
            self._anchor['text'].append(data)

    def handle_endtag(self, tag):
        if tag == 'a' and self._anchor is not None:
            self.anchors.append((self._anchor['href'], ' '.join(self._anchor['text'])))
            self._anchor = None
        if tag == 'script' and self._json is not None:
            try:
                self.documents.append(json.loads(self._json))
            except (ValueError, TypeError):
                pass
            self._json = None


def kind(node, expected):
    t = node.get('@type', '')
    return expected == t or (isinstance(t, list) and expected in t)


def nodes(document):
    # Não entra em recomendações, ItemList ou ofertas de outros produtos.
    if isinstance(document, list):
        for value in document:
            yield from nodes(value)
    elif isinstance(document, dict):
        yield document
        yield from nodes(document.get('@graph', []))
        if isinstance(document.get('mainEntity'), (dict, list)):
            yield from nodes(document['mainEntity'])


def amount(value):
    try:
        if isinstance(value, bool) or not re.fullmatch(r'\d+(?:\.\d{1,2})?', str(value)):
            raise ValueError()
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0:
            raise ValueError()
        return f'{number:,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')
    except (InvalidOperation, ValueError, TypeError):
        raise AffiliateError('Preço público inválido ou não explícito na página.') from None


def image_url(value):
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get('contentUrl') or value.get('url')
    try:
        p = urlsplit(value)
        if (p.scheme == 'https' and p.hostname and (p.hostname == 'mlstatic.com' or p.hostname.endswith('.mlstatic.com'))
                and not p.username and not p.password and p.port in (None, 443)
                and not re.search(r'[\s\\\x00-\x1f]', value)):
            return value
    except (ValueError, TypeError):
        pass
    raise AffiliateError('Imagem do produto não identificada no domínio de imagens ML.')



def social_image_url(values):
    """Escolhe uma imagem ML válida entre src/currentSrc/srcset/data-src do card."""
    if not isinstance(values, list):
        values = [values]
    candidates = []
    for value in values:
        if not isinstance(value, str):
            continue
        for candidate in re.findall(r'https://[^\s,]+', html.unescape(value)):
            candidates.append(candidate.strip().strip('"\''))
    for candidate in dict.fromkeys(candidates):
        try:
            return image_url(candidate)
        except AffiliateError:
            continue
    raise AffiliateError('Imagem do produto em destaque não identificada no domínio de imagens ML.')


def identity(url):
    if not allowed_link(url):
        return None
    found = product(url)
    if found:
        return found[0]
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    identity_text = ' '.join(
        [parsed.path] + query.get('item_id', []) + query.get('pdp_filters', [])
    )
    match = re.search(r'MLB-?(\d+)', identity_text, re.I)
    if match:
        return 'MercadoLivre:' + match[1]
    match = re.search(r'/up/(MLBU\d+)(?:/|$)', parsed.path, re.I)
    return match[1].upper() if match else None

def extract_product(text, url):
    if not allowed_link(url) or not identity(url):
        raise AffiliateError('Destino ainda não é uma página de produto único do Mercado Livre.')
    parsed = Page(text)
    products = [n for d in parsed.documents for n in nodes(d) if kind(n, 'Product')]
    # Duas descrições idênticas não tornam a página ambígua.
    products = list({json.dumps(p, sort_keys=True): p for p in products}.values())
    if len(products) != 1:
        raise AffiliateError('Página sem um único produto estruturado; vitrine ou conteúdo indisponível.')
    p = products[0]
    for declared in [p.get('url'), *parsed.canonical]:
        if declared:
            check = urljoin(url, declared)
            if not allowed_link(check) or not identity(check) or identity(check) != identity(url):
                raise AffiliateError('Identificação do produto diverge do destino do link.')
    name = p.get('name')
    if not isinstance(name, str) or not name.strip():
        raise AffiliateError('Nome do produto ausente na página.')
    offers = p.get('offers')
    offers = offers if isinstance(offers, list) else [offers]
    if len(offers) != 1 or not isinstance(offers[0], dict) or not kind(offers[0], 'Offer'):
        raise AffiliateError('Vários preços, variantes ou faixa de preços: publicação automática aguardando dados inequívocos.')
    offer = offers[0]
    if offer.get('priceCurrency') != 'BRL':
        raise AffiliateError('Preço em reais não identificado.')
    availability = str(offer.get('availability', '')).rsplit('/', 1)[-1]
    if availability != 'InStock':
        raise AffiliateError('Disponibilidade de compra não confirmada na página.')
    return dict(name=html.unescape(name.strip()), price=amount(offer.get('price')),
                price_condition='Preço público informado na página; confira as condições de pagamento.',
                price_from=False, api_image=image_url(p.get('image')), resolved_url=url,
                auto_fetched_at=time.time())



def nested_official_urls(url):
    """Extrai somente URLs HTTPS do próprio ecossistema ML embutidas em parâmetros."""
    try:
        query = parse_qs(urlsplit(url).query)
    except (ValueError, TypeError):
        return []
    values = []
    for items in query.values():
        for value in items:
            candidate = html.unescape(unquote(str(value))).strip()
            if candidate.startswith('//'):
                candidate = 'https:' + candidate
            if candidate.startswith('https://') and allowed_link(candidate):
                values.append(candidate)
    return list(dict.fromkeys(values))



def social_page(url):
    try:
        return allowed_link(url) and urlsplit(url).path.lower().startswith('/social/')
    except (ValueError, TypeError):
        return False


def featured_social_destination(text, current):
    """Na vitrine Social, segue somente o CTA único do produto em destaque."""
    if not social_page(current):
        return None
    page = Page(text)
    candidates = []
    for href, label in page.anchors:
        label = re.sub(r'\s+', ' ', html.unescape(label)).strip()
        if not re.search(r'\bir\s+para\s+(?:o\s+)?produto\b', label, re.I):
            continue
        target = urljoin(current, html.unescape(href).strip())
        if allowed_link(target):
            candidates.append(target)
    candidates = list(dict.fromkeys(candidates))
    return candidates[0] if len(candidates) == 1 else None


def next_destination(text, current):
    page = Page(text)
    redirects = list(dict.fromkeys(urljoin(current, x) for x in page.refresh))
    if len(redirects) == 1 and allowed_link(redirects[0]):
        return redirects[0]
    if social_page(current):
        # Não escolhe itens da grade "Para você/Mais vendidos/Ofertas".
        # Somente o CTA único do destaque principal é aceito.
        return featured_social_destination(text, current)
    # Uma página de produto direta não deve trocar para recomendações.
    direct = product(current) or re.search(r'/up/MLBU\d+(?:/|$)', urlsplit(current).path, re.I)
    if direct:
        return None
    candidates = {}
    for link in nested_official_urls(current) + page.canonical + page.links:
        target = urljoin(current, link)
        found = identity(target)
        if found:
            candidates.setdefault(found, target)
    if len(candidates) == 1:
        return next(iter(candidates.values()))
    return None



def http_transport(transport=None):
    if transport is not None:
        return transport
    session = requests.Session()
    cookie = reader_cookie()
    for piece in cookie.split(';'):
        if '=' not in piece:
            continue
        name, value = piece.strip().split('=', 1)
        if name:
            session.cookies.set(name, value)
    return session


def fetch_http(url, transport=None):
    transport = http_transport(transport)
    headers = request_headers()
    if isinstance(transport, requests.Session):
        headers.pop('Cookie', None)
    seen = set()
    for _ in range(7):
        if not allowed_link(url):
            host = urlsplit(url).hostname or 'inválido'
            raise AffiliateError(f'Redirecionamento ML para domínio não permitido: {host}.')
        if url in seen:
            raise AffiliateError('Redirecionamento ML circular após atualização de sessão.')
        seen.add(url)
        try:
            with transport.get(url, allow_redirects=False, stream=True, timeout=(5, 10),
                               headers=headers) as r:
                if r.status_code in (301, 302, 303, 307, 308):
                    destination = r.headers.get('Location')
                    if not destination:
                        raise AffiliateError('Redirecionamento sem destino.')
                    url = urljoin(url, destination)
                    continue
                if r.status_code != 200:
                    raise AffiliateError(f'Consulta ML retornou HTTP {r.status_code}.')
                content, size = [], 0
                for block in r.iter_content(65536):
                    size += len(block)
                    if size > 8_000_000:
                        raise AffiliateError('Página ML maior que o limite de leitura.')
                    content.append(block)
                text = b''.join(content).decode('utf-8', errors='replace')
        except requests.RequestException:
            raise AffiliateError('Consulta ML indisponível ou tempo de resposta excedido.') from None
        try:
            return extract_product(text, url)
        except AffiliateError:
            target = next_destination(text, url)
            if not target:
                raise
            url = target
    raise AffiliateError('Limite de redirecionamentos ML atingido.')




_SOCIAL_MONEY_RE = re.compile(
    r'R\$\s*(?P<integer>\d{1,3}(?:\.\d{3})*|\d+)(?:,(?P<cents>\d{2}))?',
    re.I,
)


def _social_money_value(match):
    integer = re.sub(r'\D', '', match.group('integer') or '')
    cents = match.group('cents') or '00'
    if not integer or int(integer) <= 0:
        raise AffiliateError('Preço do produto em destaque inválido.')
    return f'{int(integer):,}'.replace(',', '.') + ',' + cents


def _social_discount_prices(text):
    """Valores explicitamente ligados a "% OFF", excluindo descontos de saldo/cupom."""
    clean = re.sub(r'\s+', ' ', html.unescape(str(text or ''))).strip()
    found = []
    for match in _SOCIAL_MONEY_RE.finditer(clean):
        suffix = clean[match.end():match.end() + 120]
        discount = re.match(r'\s*\d+(?:[.,]\d+)?\s*%\s*OFF\b', suffix, re.I)
        if not discount:
            continue
        tail = suffix[discount.end():discount.end() + 80]
        if re.match(r'\s*(?:com\s+saldo|com\s+cupom|cupom\b)', tail, re.I):
            continue
        found.append(_social_money_value(match))
    return list(dict.fromkeys(found))


def _social_non_installment_prices(text):
    """Lê somente valores explícitos do bloco atual, descartando parcela/cupom."""
    clean = re.sub(r'\s+', ' ', html.unescape(str(text or ''))).strip()
    found = []
    for match in _SOCIAL_MONEY_RE.finditer(clean):
        before = clean[max(0, match.start() - 50):match.start()]
        after = clean[match.end():match.end() + 100]
        if re.search(r'(?:\b\d+\s*x|\bem\s+\d+\s*x)\s*\Z', before, re.I):
            continue
        if re.match(r'\s*\d+(?:[.,]\d+)?\s*%\s*OFF\s+(?:com\s+saldo|com\s+cupom)', after, re.I):
            continue
        if re.search(r'(?:cupom|saldo no mercado pago)\s*\Z', before, re.I):
            continue
        found.append(_social_money_value(match))
    return list(dict.fromkeys(found))


def social_featured_price(current_text, card_text):
    """Preço principal explícito do card Social; nunca usa valor de parcela."""
    for source in (current_text, card_text):
        candidates = _social_discount_prices(source)
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise AffiliateError('Mais de um preço principal com desconto foi encontrado no destaque.')

    candidates = _social_non_installment_prices(current_text)
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise AffiliateError('Bloco de preço atual ambíguo no destaque do Perfil Social.')
    raise AffiliateError('Preço principal do produto em destaque não identificado com segurança.')


def browser_social_featured(page, current):
    """Lê o card principal do Perfil Social sem navegar até o produto."""
    if not social_page(current):
        return None
    try:
        controls = page.locator('a, button')
        selected = []
        for index in range(controls.count()):
            control = controls.nth(index)
            try:
                label = re.sub(r'\\s+', ' ', control.inner_text()).strip().lower()
            except Exception:
                continue
            if label in {'ir para produto', 'ir para o produto'}:
                selected.append(control)
        if len(selected) != 1:
            raise AffiliateError('Perfil Social sem um único botão "Ir para produto" no destaque.')
        control = selected[0]
        data = control.evaluate(r"""(el) => {
            const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
            let node = el;

            for (let level = 0; level < 12 && node; level++, node = node.parentElement) {
                const titleEl =
                    node.querySelector('.poly-component__title') ||
                    node.querySelector('[class*="title"]') ||
                    node.querySelector('h1, h2, h3');

                const anchor = el.closest('a');
                const href = anchor ? anchor.href : (el.href || null);
                const title = titleEl ? clean(titleEl.textContent) : '';

                const currentPriceBlock =
                    node.querySelector('.poly-price__current') ||
                    node.querySelector('[class*="price__current"]') ||
                    node.querySelector('[class*="price-current"]');
                const currentAmount = currentPriceBlock
                    ? currentPriceBlock.querySelector('.andes-money-amount:not(.andes-money-amount--previous)')
                    : null;
                const fractionEl = currentAmount
                    ? currentAmount.querySelector('.andes-money-amount__fraction')
                    : null;
                const centsEl = currentAmount
                    ? currentAmount.querySelector('.andes-money-amount__cents')
                    : null;
                const fraction = fractionEl ? clean(fractionEl.textContent) : '';
                const cents = centsEl ? clean(centsEl.textContent) : '';
                const currentPriceText = currentPriceBlock
                    ? clean(currentPriceBlock.innerText || currentPriceBlock.textContent)
                    : '';
                const cardText = clean(node.innerText || node.textContent);

                const imageCandidates = [];
                for (const imgEl of node.querySelectorAll('img')) {
                    for (const value of [
                        imgEl.currentSrc,
                        imgEl.src,
                        imgEl.getAttribute('src'),
                        imgEl.getAttribute('data-src'),
                        imgEl.getAttribute('srcset'),
                        imgEl.getAttribute('data-srcset')
                    ]) {
                        if (value) imageCandidates.push(value);
                    }

                    const picture = imgEl.closest('picture');
                    if (picture) {
                        for (const source of picture.querySelectorAll('source')) {
                            for (const value of [
                                source.getAttribute('src'),
                                source.getAttribute('data-src'),
                                source.getAttribute('srcset'),
                                source.getAttribute('data-srcset')
                            ]) {
                                if (value) imageCandidates.push(value);
                            }
                        }
                    }
                }

                if (href && title && imageCandidates.length &&
                        (currentPriceText || fraction || cardText)) {
                    return {
                        href,
                        title,
                        fraction,
                        cents,
                        currentPriceText,
                        cardText,
                        imageCandidates
                    };
                }
            }

            return null;
        }""")
        if not isinstance(data, dict):
            raise AffiliateError('Card em destaque do Perfil Social não pôde ser lido.')
        target = str(data.get('href') or '').strip()
        if not target or not allowed_link(target) or not identity(target):
            raise AffiliateError('Botão do destaque não aponta para um produto único do Mercado Livre.')
        title = str(data.get('title') or '').strip()
        if not title:
            raise AffiliateError('Título do produto em destaque não identificado.')
        if 'currentPriceText' in data or 'cardText' in data:
            price = social_featured_price(
                data.get('currentPriceText', ''), data.get('cardText', ''))
        else:
            fraction = re.sub(r'\\D', '', str(data.get('fraction') or ''))
            cents = re.sub(r'\\D', '', str(data.get('cents') or ''))
            if not fraction:
                raise AffiliateError('Preço do produto em destaque não identificado.')
            cents = (cents[:2] if cents else '00').ljust(2, '0')
            price = f'{int(fraction):,}'.replace(',', '.') + ',' + cents
        image = social_image_url(data.get('imageCandidates') or data.get('image') or [])
        return dict(name=html.unescape(title), price=price,
                    price_condition='Preço exibido no destaque do Perfil Social; confira as condições de pagamento.',
                    price_from=False, api_image=image, resolved_url=target,
                    auto_fetched_at=time.time(), social_featured=True)
    except AffiliateError:
        raise
    except Exception:
        raise AffiliateError('Não foi possível ler o produto em destaque do Perfil Social.') from None

def browser_social_destination(page, context, current):
    """Resolve o CTA renderizado do destaque do Perfil Social."""
    if not social_page(current):
        return None
    try:
        matches = page.locator('a, button').filter(
            has_text=re.compile(r'^\s*Ir\s+para\s+(?:o\s+)?produto\s*$', re.I)
        )
        if matches.count() != 1:
            raise AffiliateError('Perfil Social sem um único botão "Ir para produto" no destaque.')
        control = matches.first
        href = control.get_attribute('href')
        if not href:
            try:
                href = control.evaluate("(el) => { const a = el.closest('a'); return a ? a.href : null; }")
            except Exception:
                href = None
        if href:
            target = urljoin(current, href)
            if not allowed_link(target):
                raise AffiliateError('Botão do destaque aponta para domínio não permitido.')
            return target

        before = page.url
        pages_before = list(context.pages)
        control.click(timeout=5000)
        try:
            page.wait_for_load_state('domcontentloaded', timeout=10000)
        except Exception:
            pass
        after = page.url
        if after != before and allowed_link(after):
            return after
        for candidate_page in context.pages:
            if candidate_page not in pages_before and allowed_link(candidate_page.url):
                return candidate_page.url
        raise AffiliateError('Botão "Ir para produto" não revelou um destino navegável.')
    except AffiliateError:
        raise
    except Exception:
        raise AffiliateError('Não foi possível resolver o botão "Ir para produto" do Perfil Social.') from None


def browser_image_allowed(url):
    try:
        image_url(url)
        return True
    except AffiliateError:
        return False


def fetch_browser(url):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise AffiliateError('Instale a leitura automática: py -m pip install -r requirements.txt e py -m playwright install chromium') from None
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    locale='pt-BR', accept_downloads=False, service_workers='block',
                    user_agent=os.getenv('ML_READER_USER_AGENT', DEFAULT_USER_AGENT),
                    extra_http_headers={'Accept-Language': 'pt-BR,pt;q=0.9,en;q=0.8'},
                )
                cookie = reader_cookie()
                if cookie:
                    browser_cookies = []
                    for piece in cookie.split(';'):
                        if '=' not in piece:
                            continue
                        name, value = piece.strip().split('=', 1)
                        if not name or name.startswith('__Host-'):
                            continue
                        for cookie_url in ('https://www.mercadolivre.com.br',
                                           'https://mercadolivre.com.br',
                                           'https://www.mercadolivre.com'):
                            browser_cookies.append({'name': name, 'value': value,
                                                    'url': cookie_url, 'secure': True})
                    if browser_cookies:
                        try:
                            context.add_cookies(browser_cookies)
                        except Exception:
                            pass
                # Navega somente na loja. Nenhuma automação de login/captcha ou compras.
                def route(request_route):
                    req = request_route.request
                    if req.is_navigation_request() and not allowed_link(req.url):
                        request_route.abort()
                    elif req.resource_type == 'image':
                        if browser_image_allowed(req.url):
                            request_route.continue_()
                        else:
                            request_route.abort()
                    elif req.resource_type in {'media', 'font'}:
                        request_route.abort()
                    else:
                        request_route.continue_()
                context.route('**/*', route)
                page = context.new_page()
                page.set_default_timeout(5000)
                seen = set()
                for _ in range(4):
                    if not allowed_link(url) or url in seen:
                        raise AffiliateError('Navegação ML externa ou circular.')
                    seen.add(url)
                    response = page.goto(url, wait_until='domcontentloaded', timeout=20000)
                    if response and response.status >= 400:
                        raise AffiliateError(f'Navegador ML retornou HTTP {response.status}; nenhuma oferta publicada.')
                    # Aguarda dados carregados por JS, com prazo limitado.
                    try:
                        page.wait_for_function("!!document.querySelector('script[type=\"application/ld+json\"]')", timeout=5000)
                    except Exception:
                        pass
                    current, text = page.url, page.content()
                    if not allowed_link(current):
                        raise AffiliateError('Navegador saiu do domínio permitido.')
                    if social_page(current):
                        return browser_social_featured(page, current)
                    try:
                        return extract_product(text, current)
                    except AffiliateError:
                        target = next_destination(text, current)
                        if not target:
                            raise
                        url = target
                raise AffiliateError('Destino ML não identificado pelo navegador.')
            finally:
                browser.close()
    except AffiliateError:
        raise
    except Exception:
        raise AffiliateError('Navegador ML indisponível ou página bloqueada. Instale Chromium: py -m playwright install chromium') from None


def enrich(offer):
    manual = offer.get('kind') == 'ml_manual_offer'
    pending = offer.get('kind') == 'ml_offer_pending' and offer.get('source') == 'telegram'
    automatic = offer.get('kind') == 'ml_offer' and offer.get('source') == 'telegram'
    if manual:
        authorized = (trusted(offer.get('chat_id')) and allowed_link(offer.get('url'))
                      and offer.get('product_id') == key(offer['url']))
    elif pending:
        authorized = (allowed_link(offer.get('url'))
                      and offer.get('product_id') == pending_key(offer['url']))
    elif automatic:
        identified = product(offer.get('url', ''))
        authorized = bool(identified and identified[1] == 'Mercado Livre'
                          and offer.get('product_id') == identified[0])
    else:
        authorized = False
    if not authorized:
        raise AffiliateError('Origem ML não autorizada para leitura automática.')
    try:
        found = fetch_http(offer['url'])
    except AffiliateError as http_error:
        try:
            found = fetch_browser(offer['url'])
        except AffiliateError as browser_error:
            raise AffiliateError(str(http_error) + ' ' + str(browser_error)) from None
    # Preço escrito na origem continua prevalecendo. Completa somente ausências.
    result = dict(offer, resolved_url=found['resolved_url'], auto_fetched_at=found['auto_fetched_at'])
    if pending:
        identified = product(found.get('resolved_url', ''))
        if not identified or identified[1] != 'Mercado Livre':
            raise AffiliateError('Link Mercado Livre resolvido, mas o produto final ainda não pôde ser identificado com segurança.')
        result.update(kind='ml_offer', product_id=identified[0], url=identified[2],
                      original_url=offer['url'], store='Mercado Livre')
    if not result.get('name'):
        result['name'] = found['name']
    if not result.get('price'):
        result.update({k: found[k] for k in ('price', 'price_condition', 'price_from')})
    if not result.get('image') and not result.get('api_image'):
        result['api_image'] = found['api_image']
    return result


class AutoReader:
    """Uma consulta em segundo plano; não para capturas nem outras publicações."""
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='ml-auto')
        self.jobs, self.cache = {}, {}

    def read(self, offer, blocking=False):
        if blocking:
            return enrich(offer)
        fingerprint = hashlib.sha256(json.dumps(offer, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        now = time.monotonic()
        self.cache = {k: v for k, v in self.cache.items() if now - v[0] < 300}
        for identity, pending in list(self.jobs.items()):
            if pending.done():
                del self.jobs[identity]
                try:
                    value = pending.result()
                except AffiliateError as error:
                    value = error
                except Exception:
                    value = AffiliateError('Falha inesperada na leitura ML; aguardando nova tentativa.')
                self.cache[identity] = (now, value)
        while len(self.cache) > 128:
            self.cache.pop(next(iter(self.cache)))
        if fingerprint in self.cache:
            value = self.cache[fingerprint][1]
            if isinstance(value, Exception):
                raise value
            return dict(value)
        job = self.jobs.get(fingerprint)
        if job is None:
            if len(self.jobs) >= 4:
                return None
            self.jobs[fingerprint] = self.executor.submit(enrich, dict(offer))
            return None
        return None

    def close(self):
        self.executor.shutdown(wait=False, cancel_futures=True)


def main():
    import argparse
    from datetime import datetime, timezone
    from pathlib import Path
    from dotenv import load_dotenv
    from mercadolivre_manual import chat_id
    load_dotenv(Path(__file__).resolve().parent / '.env', encoding='utf-8-sig')
    parser = argparse.ArgumentParser(description='Diagnostica um link ML; não publica nem escreve na fila.')
    parser.add_argument('--testar', required=True, metavar='URL')
    args = parser.parse_args()
    row = dict(kind='ml_manual_offer', source='telegram', chat_id=chat_id(), url=args.testar,
               product_id=key(args.testar), source_date=datetime.now(timezone.utc).isoformat())
    try:
        result = enrich(row)
    except AffiliateError as error:
        parser.exit(1, str(error) + '\nNada foi publicado.\n')
    print('Produto:', result['name'])
    print('Preço:', result['price'], '|', result.get('price_condition', ''))
    print('Imagem:', bool(result.get('api_image')))
    print('Link original preservado:', result['url'] == args.testar)
    print('Nada foi publicado. Reinicie monitor e publicador para usar a captura automática.')


if __name__ == '__main__':
    main()
