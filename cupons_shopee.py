"""Alertas de cupons: texto próprio, destinos verificados e links da nossa API."""
import hashlib
import html
import json
import re
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from ofertas_core import extract_links, product, coupon_page_links

SHOPEE = {'shopee.com.br', 'www.shopee.com.br'}
SHORT = {'s.shopee.com.br', 'shope.ee'}
# Encurtador presente no exemplo fornecido. Só seguimos redirects HTTP.
REDIRECTORS = {'desconto.games'}
URL_RE = re.compile(r'https?://[^\s<>]+')
COUPON_RE = re.compile(r'\b(?:cupom|cupons|voucher)\b', re.I)
CONDITION_RE = re.compile(r'R\$|\d\s*%|\b(?:OFF|selecionad\w*|categorias|acima|m[ií]nimo|v[aá]lid\w*|expira\w*|frete|primeira compra|c[oó]digo)\b', re.I)
PROMO_RE = re.compile(r'whatsapp|telegram|\bgrupos?\b|https?://|www\.|@\w+', re.I)

def allowed(url, hosts):
    try:
        p = urlsplit(url)
        return p.scheme == 'https' and p.hostname in hosts and not p.username and p.port in (None, 443)
    except (ValueError, TypeError):
        return False

def canonical_destination(url):
    """Preserva condições/IDs; remove parâmetros conhecidos de atribuição."""
    if not allowed(url, SHOPEE):
        return None
    p = urlsplit(url)
    tracking = {'af_siteid', 'af_sub_siteid', 'af_sub1', 'af_sub2', 'af_sub3',
                'af_sub4', 'af_sub5', 'affiliate_id', 'affiliateid', 'clickid',
                'uls_trackid', 'sp_atk', 'xptdk', 'smtt', 'pid', 'is_from_login'}
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if k.lower() not in tracking and not k.lower().startswith(('utm_', 'af_'))]
    # Não envia wrappers com outro destino aninhado como página de cupom.
    if any(k.lower() in {'url', 'target', 'target_url', 'redirect', 'redirect_url', 'originurl'} for k, _ in query):
        return None
    return urlunsplit(('https', 'shopee.com.br', p.path or '/', urlencode(sorted(query)), p.fragment))

def resolve_coupon(url, transport=None):
    from shopee_afiliados import AffiliateError
    if transport is None:
        import requests
        transport = requests
    seen = set()
    for _ in range(7):
        if url in seen or not allowed(url, SHOPEE | SHORT | REDIRECTORS):
            raise AffiliateError('Cupom bloqueado: destino não reconhecido ou redirecionamento circular.')
        seen.add(url)
        direct = canonical_destination(url)
        if direct:
            return direct
        try:
            with transport.get(url, allow_redirects=False, stream=True, timeout=(10, 15)) as response:
                if response.status_code not in (301, 302, 303, 307, 308):
                    raise AffiliateError('Cupom bloqueado: o encurtador não revelou o destino Shopee.')
                location = response.headers.get('Location')
                if not location:
                    raise AffiliateError('Cupom bloqueado: redirecionamento sem destino.')
                url = urljoin(url, location)
        except AffiliateError:
            raise
        except Exception:
            raise AffiliateError('Cupom bloqueado: não foi possível consultar o destino.') from None
    raise AffiliateError('Cupom bloqueado: excesso de redirecionamentos.')

def coupon_entries(messages):
    """Usa somente condições textuais vizinhas; não extrai valores da imagem."""
    text = '\n'.join(m.raw_text or '' for m in messages)
    first = next((line.strip() for line in text.splitlines() if line.strip()), '')
    general_alert = bool(COUPON_RE.search(first))
    explicitly_labeled = coupon_page_links(text)
    if not general_alert and not explicitly_labeled:
        return []
    urls = list(dict.fromkeys(u for m in messages for u in extract_links(m)))
    contexts = {}
    block = []
    for line in text.splitlines():
        found = [u.rstrip('.,;!?)\"\']') for u in URL_RE.findall(line)]
        clean = URL_RE.sub('', line).strip(' 👉🔗:-')
        if found:
            adjacent = block + ([clean] if clean else [])
            label = '\n'.join(s for s in adjacent if CONDITION_RE.search(s) and not PROMO_RE.search(s))
            for url in found:
                contexts[url] = label
            block = []
        elif not line.strip():
            block = []
        else:
            block.append(line.strip())
    result = []
    for url in urls:
        if not allowed(url, SHOPEE | SHORT | REDIRECTORS) or product(url):
            continue
        if not general_alert and url not in explicitly_labeled:
            continue
        result.append({'url': url, 'conditions': contexts.get(url, '')})
    return result

def alert_key(entries, source_date):
    day = datetime.fromisoformat(source_date).astimezone(ZoneInfo('America/Sao_Paulo')).date().isoformat()
    identity = sorted({(e['url'], e.get('conditions', '')) for e in entries})
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
    return 'ShopeeCoupon:' + day + ':' + digest

def build_alerts(messages, chat_id):
    entries = coupon_entries(messages)
    date = messages[0].date.isoformat()
    return [dict(kind='coupon_alert', source='telegram', store='Shopee',
                 entries=entries[i:i+6], product_id=alert_key(entries[i:i+6], date),
                 source_date=date, chat_id=chat_id, message_id=messages[0].id)
            for i in range(0, len(entries), 6)]

def prepare_alert(client, alert):
    from shopee_afiliados import AffiliateError
    entries = alert.get('entries')
    if not isinstance(entries, list) or not 1 <= len(entries) <= 6:
        raise AffiliateError('Alerta de cupons inválido.')
    prepared = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get('conditions', ''), str):
            raise AffiliateError('Condições do cupom inválidas.')
        destination = resolve_coupon(entry.get('url'))
        if product(destination):
            raise AffiliateError('Alerta bloqueado: um dos links é de produto, não de cupons.')
        converted = client.generate_coupon_link(destination)
        if converted == entry.get('url'):
            raise AffiliateError('Cupom bloqueado: a API devolveu o link original do grupo.')
        prepared.append(dict(url=destination, conditions=entry.get('conditions', ''),
                             affiliate_url=converted))
    output = dict(alert, entries=prepared, affiliate_generated=True,
                  product_id=alert_key(prepared, alert['source_date']))
    if len(alert_caption(output)) > 4000:
        raise AffiliateError('Alerta muito longo: condições preservadas; publicação bloqueada.')
    return output

def alert_caption(alert):
    parts = ['🎟️ <b>ALERTA DE CUPONS SHOPEE</b>']
    for index, entry in enumerate(alert['entries'], 1):
        parts.append('<b>Opção ' + str(index) + '</b>' +
                     ('\n' + html.escape(entry['conditions']) if entry.get('conditions') else '\nConfira os cupons no link abaixo.'))
    parts.append('Confira validade, disponibilidade e regras de cada cupom na Shopee.')
    parts.append('(ANÚNCIO)')
    return '\n\n'.join(parts)

def banner_path(base):
    import os
    value = os.getenv('CUPONS_BANNER', 'assets/banner_cupons.png')
    path = (base / value).resolve()
    return path if path.is_file() and 0 < path.stat().st_size <= 10_000_000 else None

def send_alert(token, channel, alert, image=None):
    import requests
    from shopee_afiliados import AffiliateError, valid_affiliate_url
    entries = alert.get('entries', [])
    if not alert.get('affiliate_generated') or not entries or any(not valid_affiliate_url(e.get('affiliate_url')) for e in entries):
        raise AffiliateError('Alerta bloqueado: faltam links gerados pela API.')
    text = alert_caption(alert)
    # Nunca corta condições para caber na legenda de uma foto.
    image = image if len(text.encode('utf-16-le')) // 2 <= 1024 else None
    data = {'chat_id': channel, 'parse_mode': 'HTML',
            'caption' if image else 'text': text,
            'reply_markup': json.dumps({'inline_keyboard': [
                [{'text': '🎟️ ACESSAR OPÇÃO ' + str(i), 'url': e['affiliate_url']}]
                for i, e in enumerate(entries, 1)]})}
    method = 'sendPhoto' if image else 'sendMessage'
    endpoint = f'https://api.telegram.org/bot{token}/{method}'
    if image:
        with image.open('rb') as photo:
            response = requests.post(endpoint, data=data, files={'photo': (image.name, photo)}, timeout=(10, 45))
    else:
        data['link_preview_options'] = json.dumps({'is_disabled': True})
        response = requests.post(endpoint, data=data, timeout=(10, 45))
    result = response.json()
    if result.get('ok') is False:
        return None, int(result.get('parameters', {}).get('retry_after', 60))
    return int(result['result']['message_id']), 0

def main():
    import argparse
    from pathlib import Path
    from dotenv import load_dotenv
    from shopee_afiliados import ShopeeAffiliate, AffiliateError
    load_dotenv(Path(__file__).resolve().parent / '.env', encoding='utf-8-sig')
    parser = argparse.ArgumentParser(description='Converte links de cupons sem publicar no Telegram.')
    parser.add_argument('--testar', nargs='+', required=True, metavar='URL')
    args = parser.parse_args()
    try:
        client = ShopeeAffiliate.from_env()
        for url in args.testar:
            destination = resolve_coupon(url)
            if product(destination):
                raise AffiliateError('Use o diagnóstico de produtos para este link.')
            print('Link gerado com suas credenciais:', client.generate_coupon_link(destination))
    except AffiliateError as error:
        parser.exit(1, str(error) + '\nNada foi publicado no Telegram.\n')
    print('Nada foi publicado no Telegram.')

if __name__ == '__main__':
    main()
