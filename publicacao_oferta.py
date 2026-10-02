"""Validação e envio Telegram de uma oferta de produto.

Módulo compartilhado pelo publicador unificado e pelo modo manual do radar,
evitando dependência do radar no processo principal do publicador.
"""
import json

import requests

import canal_espelho as mirror
import mercadolivre_manual as ml_manual
from kabum_afiliados import valid_affiliate_url as valid_kabum_affiliate_url
from mercadolivre_afiliados import valid_affiliate_url as valid_ml_affiliate_url
from ofertas_core import caption, price_info
from shopee_afiliados import AffiliateError, valid_affiliate_url
from telegram_api import send_telegram


def valid_price(offer):
    value = offer.get("price")
    if not isinstance(value, str):
        return False
    info = price_info("R$ " + value)
    return bool(
        info
        and info["price"] == value
        and not info["price_condition"]
    )


def send_offer(token, channel, offer, image):
    is_mirror = offer.get("publish_mode") == "mirror"
    if not is_mirror and not valid_price(offer):
        raise AffiliateError(
            "Publicação bloqueada: preço ausente ou inválido."
        )
    if offer.get("kind") == "ml_manual_offer":
        offer = ml_manual.prepare(offer)
    elif not offer.get("affiliate_generated"):
        raise AffiliateError(
            "Publicação bloqueada: falta link de afiliado gerado."
        )
    elif offer.get("store") == "Mercado Livre":
        if not valid_ml_affiliate_url(offer.get("affiliate_url")):
            raise AffiliateError(
                "Publicação bloqueada: link de afiliado Mercado Livre inválido."
            )
    elif offer.get("store") == "KaBuM":
        if not valid_kabum_affiliate_url(offer.get("affiliate_url")):
            raise AffiliateError(
                "Publicação bloqueada: link de afiliado KaBuM/Awin inválido."
            )
    elif not valid_affiliate_url(offer.get("affiliate_url")):
        raise AffiliateError(
            "Publicação bloqueada: falta link gerado pela API de Afiliados."
        )

    rendered = (
        mirror.render(
            offer.get("mirror_template"),
            offer.get("affiliate_url"),
        )
        if is_mirror
        else caption(offer)
    )
    if mirror.visible_length(rendered) > 1024:
        image = None

    data = {"chat_id": channel, "parse_mode": "HTML"}
    if is_mirror:
        button = str(offer.get("mirror_button_text") or "").strip()
        if button:
            data["reply_markup"] = json.dumps({
                "inline_keyboard": [[{
                    "text": button[:64],
                    "url": offer["affiliate_url"],
                }]]
            })
    else:
        data["reply_markup"] = json.dumps({
            "inline_keyboard": [[{
                "text": "🛒 VER OFERTA",
                "url": offer["affiliate_url"],
            }]]
        })

    data["caption" if image else "text"] = rendered
    message_id = send_telegram(
        requests,
        token,
        data,
        image=image,
        image_mime="image/jpeg",
    )
    return message_id, 0
