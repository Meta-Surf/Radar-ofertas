"""Publicação espelho de ofertas de um canal especial."""
import html
import os
import re
from urllib.parse import urlsplit

from shopee_afiliados import AffiliateError

PLACEHOLDER = "__RADAR_LINK_AFILIADO__"
SHOPEE_HOSTS = {"shopee.com.br", "www.shopee.com.br", "s.shopee.com.br", "shope.ee"}
ML_HOSTS = {
    "meli.la", "mercadolivre.com", "www.mercadolivre.com",
    "mercadolivre.com.br", "www.mercadolivre.com.br",
    "produto.mercadolivre.com.br",
}
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
TAG_RE = re.compile(r"<[^>]+>")
ANCHOR_RE = re.compile(r'<a href="([^"]+)">(.*?)</a>', re.I | re.S)
SOCIAL_LINE_RE = re.compile(
    r"(?i)\b(?:instagram|telegram|whatsapp|youtube|tiktok|facebook|twitter|"
    r"grupo|canal|rede social|siga(?:-nos)?|entre no grupo)\b|(?<!\w)@\w{3,}"
)


def configured_chats():
    return {v.strip() for v in os.getenv("TG_ESPELHO_CHATS", "").split(",") if v.strip()}


def supported_store_url(url):
    try:
        parsed = urlsplit(str(url))
    except (TypeError, ValueError):
        return False
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if host in SHOPEE_HOSTS:
        return parsed.path not in ("", "/")
    if host in ML_HOSTS:
        if parsed.path in ("", "/"):
            return False
        return not parsed.path.lower().startswith(("/social/", "/afiliados", "/ajuda", "/institucional"))
    return False


def message_html(message):
    raw = message.raw_text or ""
    try:
        from telethon.extensions import html as telethon_html
        return telethon_html.unparse(raw, getattr(message, "entities", None) or [])
    except Exception:
        return html.escape(raw)


def extract_all_links(messages):
    from ofertas_core import extract_links
    links = []
    for message in messages:
        links.extend(extract_links(message))
    return list(dict.fromkeys(links))


def matching_button_text(messages, source_urls):
    wanted = set(source_urls)
    labels = []
    for message in messages:
        markup = getattr(message, "reply_markup", None)
        for row in getattr(markup, "rows", []) or []:
            for button in getattr(row, "buttons", []) or []:
                if getattr(button, "url", None) in wanted:
                    label = str(getattr(button, "text", "") or "").strip()
                    if label:
                        labels.append(label[:64])
    labels = list(dict.fromkeys(labels))
    return labels[0] if len(labels) == 1 else None


def _clean_lines(value):
    output = []
    previous_blank = False
    for line in value.splitlines():
        plain = html.unescape(TAG_RE.sub("", line)).strip()
        if not plain:
            if output and not previous_blank:
                output.append("")
                previous_blank = True
            continue
        if PLACEHOLDER not in line and SOCIAL_LINE_RE.search(plain):
            continue
        output.append(line.rstrip())
        previous_blank = False
    while output and not output[-1]:
        output.pop()
    return "\n".join(output).strip()


def build_template(messages, source_urls):
    source_urls = list(dict.fromkeys(str(url) for url in source_urls if url))
    if not source_urls or any(not supported_store_url(url) for url in source_urls):
        raise AffiliateError("Canal espelho: link de loja não suportado.")

    all_links = extract_all_links(messages)
    wanted = set(source_urls)
    value = "\n".join(message_html(m) for m in messages if (m.raw_text or "").strip())

    def rewrite_anchor(match):
        href = html.unescape(match.group(1))
        body = match.group(2)
        if href in wanted:
            body = body.replace(html.escape(href, quote=True), PLACEHOLDER).replace(href, PLACEHOLDER)
            return '<a href="' + PLACEHOLDER + '">' + body + "</a>"
        if href in all_links:
            return ""
        return match.group(0)

    value = ANCHOR_RE.sub(rewrite_anchor, value)

    for url in all_links:
        encoded = html.escape(url, quote=True)
        replacement = PLACEHOLDER if url in wanted else ""
        value = value.replace(encoded, replacement).replace(url, replacement)

    value = _clean_lines(value)
    button_text = matching_button_text(messages, source_urls)
    if PLACEHOLDER not in value and not button_text:
        raise AffiliateError("Canal espelho: link da loja não está no texto nem em botão.")
    if not value:
        raise AffiliateError("Canal espelho: publicação ficou sem texto útil.")
    return value, button_text


def render(template, affiliate_url):
    if not isinstance(template, str) or not template.strip():
        raise AffiliateError("Canal espelho: texto original ausente.")
    if not isinstance(affiliate_url, str) or not affiliate_url.startswith("https://"):
        raise AffiliateError("Canal espelho: link de afiliado ausente.")
    rendered = template.replace(PLACEHOLDER, html.escape(affiliate_url, quote=True))
    if PLACEHOLDER in rendered:
        raise AffiliateError("Canal espelho: marcador de link não foi substituído.")
    urls = [html.unescape(url.rstrip(".,;!?)")) for url in URL_RE.findall(rendered)]
    if any(url != affiliate_url for url in urls):
        raise AffiliateError("Canal espelho: publicação contém link externo não autorizado.")
    return rendered


def visible_length(value):
    plain = html.unescape(TAG_RE.sub("", str(value or "")))
    return len(plain.encode("utf-16-le")) // 2
