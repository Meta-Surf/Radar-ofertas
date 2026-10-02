"""Adaptador Instagram em modo estritamente dry-run.

Não contém cliente HTTP, token Meta ou método de publicação. Ele apenas transforma
uma oferta já aprovada pelo Radar em um candidato inspecionável.
"""
import re
from pathlib import Path

URL_RE = re.compile(r"https?://\S+", re.I)
HTML_RE = re.compile(r"<[^>]+>")


def _clean(value):
    text = HTML_RE.sub("", str(value or ""))
    text = URL_RE.sub("", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _sanitize_source(value):
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if "affiliate" in lowered or "url" in lowered or "link" in lowered:
                continue
            clean[key] = _sanitize_source(item)
        return clean
    if isinstance(value, (list, tuple)):
        return [_sanitize_source(item) for item in value]
    if isinstance(value, str):
        return URL_RE.sub("", value).strip()
    return value


class InstagramDryRunAdapter:
    destination = "instagram"
    surface = "feed"
    caption_limit = 2000
    publish_enabled = False

    def _caption(self, offer):
        if offer.get("kind") == "coupon_alert":
            lines = ["🔥 Cupons e ofertas"]
            code = _clean(offer.get("code"))
            if code:
                lines.append(f"🏷️ Cupom: {code}")
            for entry in (offer.get("entries") or [])[:8]:
                if not isinstance(entry, dict):
                    continue
                condition = _clean(entry.get("conditions"))
                if condition:
                    lines.append("• " + condition)
            lines.append("Confira as condições atualizadas no Radar de Ofertas.")
            return "\n\n".join(lines)

        name = _clean(offer.get("name"))
        price = _clean(offer.get("price"))
        condition = _clean(offer.get("price_condition"))
        coupon = _clean(offer.get("coupon"))
        badge = _clean(offer.get("history_badge"))
        lines = [name] if name else []
        if price:
            lines.append(f"🔥 R$ {price}")
        if condition:
            lines.append(condition)
        if coupon:
            lines.append(f"🏷️ Cupom: {coupon}")
        if badge:
            lines.append(badge)
        lines.append("Oferta sujeita a alteração de preço e estoque.")
        return "\n\n".join(line for line in lines if line)
    def _media(self, image):
        raw = str(image or "").strip()
        if not raw:
            return "", "missing"
        if raw.startswith("https://"):
            return raw, "remote_ready"
        path = Path(raw)
        if path.is_file() and 0 < path.stat().st_size <= 20_000_000:
            return str(path.resolve()), "local_ready_for_upload"
        return raw, "invalid"

    def prepare(self, offer, image=None):
        caption = self._caption(offer)
        media_ref, media_state = self._media(image)
        reasons = []
        if not caption:
            reasons.append("CAPTION_VAZIA")
        if len(caption) > self.caption_limit:
            reasons.append("CAPTION_LONGA")
        if media_state in {"missing", "invalid"}:
            reasons.append("MIDIA_INDISPONIVEL")

        sanitized = _sanitize_source(dict(offer))

        return {
            "destination": self.destination,
            "surface": self.surface,
            "state": "READY" if not reasons else "BLOCKED",
            "reasons": reasons,
            "caption": caption[: self.caption_limit],
            "caption_length": len(caption),
            "media_ref": media_ref,
            "media_state": media_state,
            "affiliate_state": "DESTINATION_ATTRIBUTION_REQUIRED",
            "publish_enabled": False,
            "source_offer": sanitized,
        }

    def publish(self, *args, **kwargs):
        raise RuntimeError(
            "Instagram está em dry-run: publicação de rede desabilitada por projeto."
        )
