"""Cupons oficiais KaBuM obtidos exclusivamente pela Offers API da Awin."""
import html
import copy
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests

from kabum_afiliados import attributed_affiliate_url, valid_affiliate_url
from telegram_api import send_telegram
from ofertas_core import product
from shopee_afiliados import AffiliateError

ADVERTISER_ID = "17729"
KABUM_HOSTS = {"kabum.com.br", "www.kabum.com.br"}
CODE_RE = re.compile(r"^[A-Za-z0-9_-]{3,40}$")
URL_RE = re.compile(r"https?://[^\s<>]+", re.I)
MAX_START_AGE_DAYS = 90


def _dt(value):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.utcoffset() is not None else None
    except (TypeError, ValueError):
        return None


def clean_text(value):
    value = html.unescape(str(value or ""))
    value = re.sub(r"<[^>]*>", "", value)
    value = URL_RE.sub("", value)
    value = re.sub(r"\s+", " ", value).strip(" .\t\r\n")
    return value


def safe_destination(url):
    try:
        p = urlsplit(str(url or ""))
        return (
            p.scheme == "https"
            and (p.hostname or "").lower() in KABUM_HOSTS
            and not p.username and not p.password
            and p.port in (None, 443)
            and bool(p.path)
        )
    except (TypeError, ValueError):
        return False


def eligible(item, now=None):
    """Aceita somente voucher oficial, atual, não ligado diretamente a produto."""
    try:
        commercial_unit(item)
    except (TypeError, ValueError):
        return False
    advertiser = item.get("advertiser") or {}
    if str(advertiser.get("id") or "") != ADVERTISER_ID or advertiser.get("joined") is False:
        return False
    promotion_id = str(item.get("promotionId") or "").strip()
    voucher = item.get("voucher") or {}
    code = str(voucher.get("code") or "").strip()
    destination = str(item.get("url") or "").strip()
    if not promotion_id.isdigit() or not CODE_RE.fullmatch(code):
        return False
    if not safe_destination(destination) or product(destination):
        return False

    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start, end = _dt(item.get("startDate")), _dt(item.get("endDate"))
    if not start or not end or not (start <= now < end):
        return False
    # Awin mantém alguns vouchers antigos como "ativos" mesmo com texto contraditório.
    if start < now - timedelta(days=MAX_START_AGE_DAYS):
        return False
    if not clean_text(item.get("title") or item.get("description")):
        return False
    return True


def commercial_unit(item):
    """Identidade nativa, sem relógio, tracking ou metadados de observação."""
    from awin_kabum import AwinKabumAPI
    if (not isinstance(item, dict) or not isinstance(item.get('advertiser'), dict)
            or not isinstance(item.get('voucher'), dict)
            or not AwinKabumAPI._official_kabum_offer(item) or item.get('type') != 'voucher'):
        raise ValueError('Voucher oficial ausente')
    pid = str(item.get('promotionId') or '').strip()
    code = str(item['voucher'].get('code') or '').strip()
    target = str(item.get('url') or '').strip()
    start, end = _dt(item.get('startDate')), _dt(item.get('endDate'))
    if (not pid.isdigit() or not CODE_RE.fullmatch(code) or not safe_destination(target)
            or product(target) or not start or not end or start >= end
            or not clean_text(item.get('title') or item.get('description'))):
        raise ValueError('Unidade comercial incompleta')
    return {'advertiser_id': ADVERTISER_ID, 'promotion_id': pid, 'type': 'voucher',
            'code': code, 'destination_url': target, 'start_date': start.isoformat(),
            'end_date': end.isoformat(),
            # Preserva também URLs/condições que não aparecem na legenda limpa.
            **{key: str(item.get(key) or '').strip() for key in ('title', 'description', 'terms')}}


def alert_unit(alert):
    if not is_native_alert(alert) or not isinstance(alert.get('kabum_coupon_unit'), dict):
        raise ValueError('Evidência nativa ausente')
    unit = alert['kabum_coupon_unit']
    normalized = commercial_unit({'advertiser': {'id': unit.get('advertiser_id')},
        'promotionId': unit.get('promotion_id'), 'type': unit.get('type'),
        'voucher': {'code': unit.get('code')}, 'url': unit.get('destination_url'),
        'startDate': unit.get('start_date'), 'endDate': unit.get('end_date'),
        **{key: unit.get(key) for key in ('title', 'description', 'terms')}})
    if (normalized != unit or alert.get('product_id') != 'KaBuMCoupon:' + unit['promotion_id']
            or any(alert.get(key) != unit[key] for key in
                   ('promotion_id', 'code', 'destination_url', 'start_date', 'end_date'))
            or any(alert.get(key) != clean_text(unit[key]) for key in ('title', 'description', 'terms'))):
        raise ValueError('Alerta diverge da unidade nativa')
    return copy.deepcopy(unit)


def is_native_alert(alert):
    return (isinstance(alert, dict) and alert.get('kind') == 'coupon_alert'
            and alert.get('source') == 'kabum_awin_coupon' and alert.get('store') == 'KaBuM')


def period_reason(unit, now):
    if not math.isfinite(now):
        raise ValueError('Relógio inválido')
    if now < _dt(unit['start_date']).timestamp():
        return 'CUPOM_NAO_INICIADO'
    return 'CUPOM_EXPIRADO' if now >= _dt(unit['end_date']).timestamp() else ''


def _approved_content(alert):
    return copy.deepcopy({key: alert.get(key) for key in (
        'kind', 'source', 'store', 'product_id', 'promotion_id', 'code', 'destination_url',
        'start_date', 'end_date', 'title', 'description', 'terms', 'affiliate_url',
        'affiliate_generated', 'kabum_coupon_unit')})


def build_proof(original, approved, selected=None):
    from cupom_validade import fingerprint
    unit = alert_unit(original)
    if alert_unit(approved) != unit or not valid_affiliate_url(approved.get('affiliate_url')):
        raise ValueError('Oferta final não corresponde à revisão comercial')
    content = _approved_content(approved)
    body = {'version': 1, 'selection': copy.deepcopy(selected), 'unit': unit,
            'unit_digest': fingerprint(unit), 'content': content, 'content_digest': fingerprint(content)}
    return dict(body, digest=fingerprint(body))


def authorization_status(proof, original, approved, selected, product_id, clock):
    """Conferência local sob BEGIN IMMEDIATE; lê o relógio por último."""
    from cupom_validade import fingerprint
    try:
        if (not isinstance(proof, dict) or set(proof) != {
                'version', 'selection', 'unit', 'unit_digest', 'content', 'content_digest', 'digest'}
                or type(proof['version']) is not int or proof['version'] != 1
                or not isinstance(selected, dict) or proof['selection'] != selected):
            raise ValueError('Prova/seleção inválida')
        unit, content = alert_unit(original), _approved_content(approved)
        body = {key: value for key, value in proof.items() if key != 'digest'}
        if (proof['digest'] != fingerprint(body) or proof['unit'] != unit
                or proof['unit_digest'] != fingerprint(unit) or alert_unit(approved) != unit
                or proof['content'] != content or proof['content_digest'] != fingerprint(content)
                or content['product_id'] != product_id or not valid_affiliate_url(content['affiliate_url'])
                or content['affiliate_generated'] is not True):
            raise ValueError('Prova não corresponde ao conteúdo final')
        return period_reason(unit, clock())
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return 'CUPOM_VALIDADE_NAO_CONFIRMADA'


def alert_from_offer(item, affiliate, now=None):
    if not eligible(item, now):
        raise AffiliateError("Cupom KaBuM/Awin não atende aos critérios de publicação.")
    promotion_id = str(item["promotionId"])
    voucher = item.get("voucher") or {}
    destination = str(item.get("url") or "").strip()
    tracking = str(item.get("urlTracking") or "").strip()
    if not valid_affiliate_url(tracking):
        tracking = affiliate._link_builder(destination)
    if not valid_affiliate_url(tracking):
        raise AffiliateError("Cupom KaBuM bloqueado: link Awin inválido.")
    start, end = _dt(item.get("startDate")), _dt(item.get("endDate"))
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    hours_left = max(0.0, (end - now).total_seconds() / 3600)
    urgency = max(0.0, 72.0 - min(hours_left, 72.0))
    return {
        "kind": "coupon_alert",
        "source": "kabum_awin_coupon",
        "store": "KaBuM",
        "product_id": "KaBuMCoupon:" + promotion_id,
        "promotion_id": promotion_id,
        "code": str(voucher.get("code") or "").strip(),
        "title": clean_text(item.get("title")),
        "description": clean_text(item.get("description")),
        "terms": clean_text(item.get("terms")),
        "destination_url": destination,
        "affiliate_url": tracking,
        "affiliate_generated": True,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "source_date": now.isoformat(),
        "radar_score": 1000.0 + urgency,
        "queue_expires_at": end.timestamp(),
        "kabum_coupon_unit": commercial_unit(item),
    }


def collect_alerts(offers, affiliate, now=None):
    alerts = []
    for item in offers or []:
        try:
            alerts.append(alert_from_offer(item, affiliate, now))
        except AffiliateError:
            continue
    alerts.sort(key=lambda a: (-a["radar_score"], a["product_id"]))
    return alerts


def prepare_alert(affiliate, alert, now=None, destination="telegram"):
    if not isinstance(alert, dict) or alert.get("store") != "KaBuM":
        raise AffiliateError("Alerta KaBuM inválido.")
    if alert.get("source") != "kabum_awin_coupon":
        raise AffiliateError("Origem do cupom KaBuM inválida.")
    promotion_id = str(alert.get("promotion_id") or "").strip()
    code = str(alert.get("code") or "").strip()
    if alert.get("product_id") != "KaBuMCoupon:" + promotion_id:
        raise AffiliateError("Identidade do cupom KaBuM inconsistente.")
    if not promotion_id.isdigit() or not CODE_RE.fullmatch(code):
        raise AffiliateError("Código do cupom KaBuM inválido.")
    end = _dt(alert.get("end_date"))
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not end or now >= end:
        raise AffiliateError("Cupom KaBuM expirado.")
    link = str(alert.get("affiliate_url") or "").strip()
    target = str(alert.get("destination_url") or "")
    if not valid_affiliate_url(link):
        if not safe_destination(target):
            raise AffiliateError("Destino do cupom KaBuM inválido.")
        link = affiliate._link_builder(target, destination=destination)
    link = attributed_affiliate_url(link, destination)
    if not valid_affiliate_url(link):
        raise AffiliateError("Cupom KaBuM sem tracking Awin válido.")
    return dict(alert, affiliate_url=link, affiliate_generated=True)


def _br_datetime(value):
    dt = _dt(value)
    if not dt:
        return ""
    local = dt.astimezone(ZoneInfo("America/Sao_Paulo"))
    return local.strftime("%d/%m/%Y às %H:%M")


def alert_caption(alert):
    title = clean_text(alert.get("title"))
    description = clean_text(alert.get("description"))
    terms = clean_text(alert.get("terms"))
    parts = ["🔥 <b>Cupom Oficial KaBuM</b>"]
    if title:
        parts.append("🏷️ " + html.escape(title))
    if description and description.casefold() != title.casefold():
        parts.append(html.escape(description))
    parts.append("🎟️ Código: <code>" + html.escape(str(alert["code"])) + "</code>")
    valid_until = _br_datetime(alert.get("end_date"))
    if valid_until:
        parts.append("⏰ Válido até " + html.escape(valid_until) + " (horário de Brasília).")
    if terms and terms not in {".", ".."}:
        parts.append("📌 " + html.escape(terms))
    parts.append("Confira produtos participantes, disponibilidade e demais regras na KaBuM.")
    parts.append("🛒 Resgate aqui:\n" + html.escape(str(alert["affiliate_url"])))
    parts.append("#anuncio")
    return "\n\n".join(parts)


def visible_length(text):
    clean = re.sub(r"</?(?:code|b)>", "", text)
    return len(html.unescape(clean).encode("utf-16-le")) // 2


def banner_path(base):
    path = Path(base) / "assets/banner_cupons_kabum.png"
    return path if path.is_file() and 0 < path.stat().st_size <= 10_000_000 else None


def send_alert(token, channel, alert, image=None):
    text = alert_caption(alert)
    if visible_length(text) > 4096:
        raise AffiliateError("Cupom KaBuM excede o limite do Telegram.")
    if visible_length(text) > 1024:
        image = None
    data = {
        "chat_id": channel,
        "parse_mode": "HTML",
        "caption" if image else "text": text,
    }
    message_id = send_telegram(
        requests, token, data, image=image, image_mime="image/png"
    )
    return message_id, 0
