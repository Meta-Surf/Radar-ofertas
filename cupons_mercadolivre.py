"""Listas completas de códigos ML, sem links ou atribuição de terceiros."""
import hashlib
import html
import json
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit

import requests
from shopee_afiliados import AffiliateError

# Inclui links sem protocolo, Markdown, HTML e convites/usuários de grupos.
LINK = re.compile(r'(?i)(?:https?://|www\.)[^\s<>]+|\b(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s<>]*)?|@\w+')
PROMO = re.compile(r'(?i)\b(?:grupos?|whatsapp|telegram|canal|afiliad\w*|compre aqui|acesse aqui|siga|participe|entre aqui)\b')
DEFAULT_SOCIAL_URL = 'https://www.mercadolivre.com.br/social/bebidastaio'
CTA_ONLY = re.compile(r'^(?:[\W_]*)(?:link(?: aqui)?|(?:resgate|resgatar|acesse|ative|ativar)(?: aqui)?(?: (?:os|seus))?(?: cupons?)?)[\s:!➡👉🔗🎟️-]*$', re.I)


def social_url():
    value = os.getenv('ML_CUPONS_SOCIAL_URL', '').strip() or DEFAULT_SOCIAL_URL
    try:
        p = urlsplit(value)
        valid = (p.scheme == 'https' and p.hostname in {'www.mercadolivre.com.br', 'mercadolivre.com.br'}
                 and not p.username and not p.password and p.port in (None, 443)
                 and re.fullmatch(r'/social/[A-Za-z0-9_-]+/?', p.path)
                 and not re.search(r'[\s\\\x00-\x1f]', value))
    except ValueError:
        valid = False
    if not valid:
        raise AffiliateError('ML_CUPONS_SOCIAL_URL inválido: informe a URL HTTPS do seu Social Mercado Livre.')
    return value


CODE = re.compile(r':\s*([A-Z][A-Z0-9_-]{3,39})\s*$')


def clean_text(text):
    text = html.unescape(html.unescape(text))
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'<[^>]*>', '', text)
    text = re.sub('[\u200b-\u200f\ufeff]', '', text)
    text = text.replace('\\', '').replace('`', '').replace('**', '').replace('__', '')
    lines = []
    for line in text.splitlines():
        # Link na mesma linha de cupom: só a URL é descartada.
        line = LINK.sub('', line).strip()
        if CTA_ONLY.fullmatch(line):
            continue
        if not re.search(r'\w', line):
            continue
        if PROMO.search(line) and not (re.search(r'\d\s*%', line) and CODE.search(line)):
            continue
        lines.append(re.sub(r'[ \t]+', ' ', line))
    return '\n\n'.join(lines)


def entries(text):
    result = []
    for line in text.splitlines():
        found = CODE.search(line)
        if found and re.search(r'\d\s*%|\bOFF\b', line, re.I):
            result.append({'code': found[1], 'conditions': line[:found.start()].strip(' ✅🏷️🔥:')})
    return result


def alert_key(items, date, text):
    day = datetime.fromisoformat(date).astimezone(ZoneInfo('America/Sao_Paulo')).date().isoformat()
    # Preserva qualificadores globais na identidade para não perder mudanças de regra.
    conditions = sorted(line.strip() for line in text.splitlines() if line.strip())
    digest = hashlib.sha256(json.dumps(conditions, ensure_ascii=False).encode()).hexdigest()
    return 'MLCoupon:' + day + ':' + digest


def build_alert(messages, chat_id):
    raw = '\n'.join(m.raw_text or '' for m in messages)
    text = clean_text(raw)
    first = next((line for line in text.splitlines() if line.strip()), '')
    heading = re.sub(r'^[^\w]+', '', first)
    general = bool(re.fullmatch(r'mercado\s+livre[ !:—-]*(?:em selecionados[ !]*)?', heading, re.I)
                   or (re.search(r'\b(?:cupom|cupons)\b', heading, re.I)
                       and re.search(r'\bmercado\s+livre\b', heading, re.I)))
    if not general:
        return None
    items = entries(text)
    if not items:
        return None
    date = messages[0].date.isoformat()
    return dict(kind='coupon_alert', source='telegram', store='Mercado Livre',
                entries=items, text=text, product_id=alert_key(items, date, text),
                source_date=date, chat_id=chat_id, message_id=min(m.id for m in messages))


def alert_caption(alert):
    text = clean_text(alert['text'])
    source_lines = [line for line in text.splitlines() if line.strip()]
    parts = ['🔥 <b>Cupom Mercado Livre</b>']

    start = 0
    if source_lines:
        heading = re.sub(r'^[^\\w]+', '', source_lines[0]).strip()
        if re.fullmatch(r'mercado\\s+livre[ !:—-]*', heading, re.I):
            start = 1
        else:
            selected = re.fullmatch(r'mercado\\s+livre\\s+em selecionados[ !:—-]*', heading, re.I)
            if selected:
                start = 1
                parts.append('Em selecionados!')
            elif (re.search(r'\\b(?:cupom|cupons)\\b', heading, re.I)
                  and re.search(r'\\bmercado\\s+livre\\b', heading, re.I)):
                start = 1

    for line in source_lines[start:]:
        if re.fullmatch(r'#?an[uú]ncio', line.strip(), re.I):
            continue
        match = CODE.search(line)
        if match:
            parts.append(html.escape(line[:match.start(1)]) + '<code>' + html.escape(match[1]) + '</code>')
        else:
            parts.append(html.escape(line))

    parts.append('Confira validade, disponibilidade e regras de cada cupom no Mercado Livre.')
    parts.append('#anuncio')
    parts.append('Resgate aqui:\n' + html.escape(social_url()))
    return '\n\n'.join(parts)


def visible_length(text):
    return len(html.unescape(re.sub(r'</?(?:code|b)>', '', text)).encode('utf-16-le')) // 2


def prepare_alert(alert):
    text = clean_text(alert.get('text', ''))
    items = entries(text)
    if not items or not re.search(r'\bmercado\s+livre\b', text, re.I):
        raise AffiliateError('Lista de cupons Mercado Livre inválida.')
    result = dict(alert, text=text, entries=items,
                  product_id=alert_key(items, alert['source_date'], text))
    result.pop('manual_links', None)
    if visible_length(alert_caption(result)) > 4096:
        raise AffiliateError('Lista ML excede 4096 caracteres: mantida na fila, sem cortar ou dividir cupons.')
    return result


def banner_path(base):
    path = Path(base) / 'assets/banner_cupons_ml.png'
    if not path.is_file() and (Path(base) / 'assets/banner_cupons_ml.parts/manifest.json').is_file():
        from banner_asset import restore_banner
        path = restore_banner(base, 'banner_cupons_ml')
    return path if path.is_file() and 0 < path.stat().st_size <= 10_000_000 else None


def send_alert(token, channel, alert, image=None):
    alert = prepare_alert(alert)
    text = alert_caption(alert)
    if visible_length(text) > 1024:
        image = None
    method = 'sendPhoto' if image else 'sendMessage'
    data = {'chat_id': channel, 'parse_mode': 'HTML', 'caption' if image else 'text': text}
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
