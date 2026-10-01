"""APIs opcionais da Awin para KaBuM: Offers e Enhanced Feed."""
import json
import os
from urllib.parse import urlparse

import requests

from ofertas_core import product

API = "https://api.awin.com"
ADVERTISER_ID = 17729


class AwinKabumAPI:
    def __init__(self, publisher_id="", access_token="", timeout=30, locale="pt_BR"):
        self.publisher_id = str(publisher_id or "").strip()
        self.access_token = str(access_token or "").strip()
        self.timeout = max(5, int(timeout))
        self.locale = str(locale or "pt_BR").strip()

    @classmethod
    def from_env(cls):
        return cls(
            publisher_id=os.getenv("AWIN_PUBLISHER_ID", ""),
            access_token=os.getenv("AWIN_ACCESS_TOKEN", ""),
            timeout=os.getenv("AWIN_API_TIMEOUT", "30") or 30,
            locale=os.getenv("KABUM_ENHANCED_LOCALE", "pt_BR") or "pt_BR",
        )

    @property
    def enabled(self):
        return self.publisher_id.isdigit() and bool(self.access_token)

    @property
    def headers(self):
        return {"Authorization": f"Bearer {self.access_token}"}

    def offers(self):
        """Retorna somente ofertas oficiais ativas da KaBuM para parceria joined."""
        if not self.enabled:
            return []
        endpoint = f"{API}/publisher/{self.publisher_id}/promotions"
        page = 1
        output = []
        while page <= 20:
            body = {
                "filters": {
                    "advertiserIds": [ADVERTISER_ID],
                    "membership": "joined",
                    "regionCodes": ["BR"],
                    "status": "active",
                    "type": "all",
                },
                "pagination": {"page": page, "pageSize": 200},
            }
            response = requests.post(
                endpoint, json=body, headers=self.headers, timeout=self.timeout
            )
            response.raise_for_status()
            payload = response.json()
            items = self._offer_items(payload)
            output.extend(x for x in items if self._official_kabum_offer(x))
            if len(items) < 200:
                break
            page += 1
        return output
    @staticmethod
    def _offer_items(payload):
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            return []
        for key in ("promotions", "offers", "data", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        if payload.get("promotionId"):
            return [payload]
        return []

    @staticmethod
    def _official_kabum_offer(item):
        if not isinstance(item, dict):
            return False
        advertiser = item.get("advertiser") or {}
        return (
            str(advertiser.get("id") or "") == str(ADVERTISER_ID)
            and advertiser.get("joined") is not False
            and item.get("type") in {"promotion", "voucher"}
        )

    def product_offer_map(self, offers=None):
        """Associa oferta oficial somente quando a URL aponta ao mesmo produto KaBuM."""
        mapped = {}
        for item in (self.offers() if offers is None else offers):
            target = product(str(item.get("url") or ""))
            if not target or target[1] != "KaBuM":
                continue
            voucher = item.get("voucher") or {}
            code = str(voucher.get("code") or "").strip()
            mapped.setdefault(target[0], []).append({
                "promotion_id": item.get("promotionId"),
                "type": item.get("type"),
                "title": str(item.get("title") or "").strip(),
                "description": str(item.get("description") or "").strip(),
                "terms": str(item.get("terms") or "").strip(),
                "start_date": item.get("startDate"),
                "end_date": item.get("endDate"),
                "coupon": code if item.get("type") == "voucher" else "",
                "tracking_url": str(item.get("urlTracking") or "").strip(),
            })
        return mapped

    def enhanced_availability(self):
        """Baixa Enhanced Feed e devolve product_id -> availability. Ausência = desconhecido."""
        if not self.enabled:
            return {}
        endpoint = (
            f"{API}/publishers/{self.publisher_id}/awinfeeds/download/"
            f"{ADVERTISER_ID}-retail-{self.locale}.jsonl"
        )
        response = requests.get(endpoint, headers=self.headers, timeout=self.timeout)
        if response.status_code == 404:
            return {}
        response.raise_for_status()
        result = {}
        for line in response.text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and "error" in row:
                return {}
            basic = row.get("product_basic") if isinstance(row, dict) else None
            source = basic if isinstance(basic, dict) else row
            if not isinstance(source, dict):
                continue
            pid = str(source.get("id") or "").strip()
            availability = str(
                source.get("availability")
                or (row.get("availability") if isinstance(row, dict) else "")
                or ""
            ).strip().lower()
            if pid and availability:
                result[pid] = availability
        return result


def stock_allows_publication(value):
    """Bloqueia somente indisponibilidade explícita; vazio permanece desconhecido."""
    value = str(value or "").strip().lower().replace(" ", "_")
    if value in {"out_of_stock", "out_ouf_stock", "unavailable", "0", "false", "no"}:
        return False
    return True


def stock_confirmed(value):
    return str(value or "").strip().lower().replace(" ", "_") in {
        "in_stock", "1", "true", "yes"
    }
