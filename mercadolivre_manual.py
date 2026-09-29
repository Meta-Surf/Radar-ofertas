"""Entrada manual ML autorizada pelo operador; preserva o endereço compartilhado."""
import hashlib
import html
import os
import re
from urllib.parse import urlsplit
from ofertas_core import extract_links, price_info, coupon
from shopee_afiliados import AffiliateError

DEFAULT_CHAT = '-1003988174916'


def chat_id():
    return os.getenv('ML_MANUAL_CHAT', DEFAULT_CHAT).strip()


def trusted(chat):
    return bool(chat_id()) and str(chat) == chat_id()


def official_ml_host(host):
    host = str(host or '').lower().rstrip('.')
    return (host == 'meli.la'
            or host in {'mercadolivre.com', 'mercadolivre.com.br'}
            or host.endswith('.mercadolivre.com')
            or host.endswith('.mercadolivre.com.br'))


def allowed_link(url):
    try:
        p = urlsplit(url)
        return (p.scheme == 'https' and not p.username and not p.password
                and p.port in (None, 443) and not re.search(r'[\s\\\x00-\x1f]', url)
                and official_ml_host(p.hostname) and p.path not in ('', '/'))
    except (ValueError, TypeError):
        return False

def links(messages):
    return list(dict.fromkeys(u for m in messages for u in extract_links(m) if allowed_link(u)))


def key(url):
    return 'MLManual:' + hashlib.sha256(url.encode()).hexdigest()


def build_offer(messages, chat):
    if not trusted(chat):
        return None
    urls = links(messages)
    if len(urls) != 1:
        raise AffiliateError('Entrada manual ML: envie um único link Mercado Livre por oferta.')
    text = html.unescape('\n'.join(m.raw_text or '' for m in messages))
    lines = [re.sub(r'[*_`]', '', s).strip() for s in text.splitlines()]
    title = next((s for s in lines if s and not s.lower().startswith(('visite a página', 'encontre todos os produtos')) and not re.search(r'https?://|www\.|R\$|\b(?:cupom|compre|link|grupo|por|de|agora)\s*:', s, re.I)), '')
    info = price_info(text) or {}
    return dict(kind='ml_manual_offer', source='telegram', store='Mercado Livre',
                product_id=key(urls[0]), url=urls[0], name=title,
                price=info.get('price'), price_condition=info.get('price_condition', ''),
                price_from=info.get('price_from', False), coupon=coupon(text),
                chat_id=chat, message_id=min(m.id for m in messages),
                source_date=messages[0].date.isoformat(), image=None)


def prepare(offer):
    if (offer.get('kind') != 'ml_manual_offer' or not trusted(offer.get('chat_id'))
            or offer.get('source') != 'telegram' or not allowed_link(offer.get('url'))
            or offer.get('product_id') != key(offer['url']) or not offer.get('name')):
        raise AffiliateError('Oferta manual ML bloqueada: origem ou endereço inválido.')
    info = price_info('R$ ' + str(offer.get('price', '')))
    if not info or info['price'] != offer.get('price') or info['price_condition']:
        raise AffiliateError('Oferta manual ML aguardando preço explícito e sem ambiguidade.')
    # Não afirma que uma API gerou ou verificou a atribuição deste link.
    return dict(offer, store='Mercado Livre', affiliate_url=offer['url'],
                affiliate_generated=False, manual_link_preserved=True)


def coupon_links(alert):
    values = alert.get('manual_links', [])
    if not values:
        return []
    if not trusted(alert.get('chat_id')) or not isinstance(values, list) or any(not allowed_link(v) for v in values):
        raise AffiliateError('Links manuais de cupons ML com origem ou destino inválido.')
    return list(dict.fromkeys(values))


def build_coupon(messages, chat):
    from cupons_mercadolivre import build_alert
    result = build_alert(messages, chat)
    if not result and trusted(chat):
        from types import SimpleNamespace
        text = '\n'.join(m.raw_text or '' for m in messages)
        first = next((s for s in text.splitlines() if s.strip()), '')
        if re.search(r'\b(?:cupom|cupons)\b', first, re.I) and price_info(text) is None:
            copy = [SimpleNamespace(raw_text='MERCADO LIVRE\n' + text,
                                    date=messages[0].date, id=min(m.id for m in messages))]
            result = build_alert(copy, chat)
    if result and trusted(chat):
        result['manual_links'] = links(messages)
    return result
