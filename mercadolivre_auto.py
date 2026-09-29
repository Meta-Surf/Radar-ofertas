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
from urllib.parse import parse_qs, urljoin, urlsplit

import requests
from mercadolivre_manual import allowed_link, trusted, key
from ofertas_core import product
from shopee_afiliados import AffiliateError

DEFAULT_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'
)


def request_headers():
    headers = {
        'User-Agent': os.getenv('ML_AFFILIATE_USER_AGENT', DEFAULT_USER_AGENT),
        'Accept-Language': 'pt-BR,pt;q=0.9,en;q=0.8',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    }
    cookie = os.getenv('ML_AFFILIATE_COOKIE', '').strip()
    if cookie and '\r' not in cookie and '\n' not in cookie and len(cookie) <= 100000:
        headers['Cookie'] = cookie
    return headers



class Page(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.documents, self.links, self.canonical = [], [], []
        self.refresh, self._json = [], None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'script' and a.get('type', '').split(';')[0] == 'application/ld+json':
            self._json = ''
        if tag == 'a' and a.get('href'):
            self.links.append(a['href'])
        if tag == 'link' and a.get('rel') == 'canonical' and a.get('href'):
            self.canonical.append(a['href'])
        if tag == 'meta' and a.get('http-equiv', '').lower() == 'refresh':
            match = re.search(r'url\s*=\s*[\"\']?([^\"\']+)', a.get('content', ''), re.I)
            if match:
                self.refresh.append(html.unescape(match[1].strip()))

    def handle_data(self, data):
        if self._json is not None:
            self._json += data

    def handle_endtag(self, tag):
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


def next_destination(text, current):
    page = Page(text)
    redirects = list(dict.fromkeys(urljoin(current, x) for x in page.refresh))
    if len(redirects) == 1 and allowed_link(redirects[0]):
        return redirects[0]
    # Nunca segue recomendação de uma página de produto cujo preço não foi lido.
    if identity(current):
        return None
    candidates = {}
    for link in page.canonical + page.links:
        target = urljoin(current, link)
        found = identity(target)
        if found:
            candidates.setdefault(found, target)
    if len(candidates) == 1:
        return next(iter(candidates.values()))
    return None


def fetch_http(url, transport=None):
    transport = transport or requests
    seen = set()
    for _ in range(7):
        if not allowed_link(url):
            host = urlsplit(url).hostname or 'inválido'
            raise AffiliateError(f'Redirecionamento ML para domínio não permitido: {host}.')
        if url in seen:
            raise AffiliateError('Redirecionamento ML circular.')
        seen.add(url)
        try:
            with transport.get(url, allow_redirects=False, stream=True, timeout=(5, 10),
                               headers=request_headers()) as r:
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
                    user_agent=os.getenv('ML_AFFILIATE_USER_AGENT', DEFAULT_USER_AGENT),
                    extra_http_headers={'Accept-Language': 'pt-BR,pt;q=0.9,en;q=0.8'},
                )
                cookie = os.getenv('ML_AFFILIATE_COOKIE', '').strip()
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
                    elif req.resource_type in {'image', 'media', 'font'}:
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
    automatic = offer.get('kind') == 'ml_offer' and offer.get('source') == 'telegram'
    if manual:
        authorized = (trusted(offer.get('chat_id')) and allowed_link(offer.get('url'))
                      and offer.get('product_id') == key(offer['url']))
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
    # Preço escrito pelo operador continua prevalecendo. Completa somente ausências.
    result = dict(offer, resolved_url=found['resolved_url'], auto_fetched_at=found['auto_fetched_at'])
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
