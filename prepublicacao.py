"""Gate central de validação imediatamente antes da publicação."""
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ofertas_core import product
from inteligencia_ofertas import Intelligence, cents
from shopee_afiliados import AffiliateError, valid_affiliate_url as valid_shopee_link
from mercadolivre_afiliados import valid_affiliate_url as valid_ml_link
from kabum_afiliados import valid_affiliate_url as valid_kabum_link
import cupons_kabum
import cupons_mercadolivre as ml_coupons


class GateReject(AffiliateError):
    def __init__(self, reason, message, *, discard=False, retry_after=300):
        super().__init__(message)
        self.reason = str(reason)
        self.discard = bool(discard)
        self.retry_after = max(0, int(retry_after))


class PrePublicationGate:
    def __init__(self, base, db, shopee=None, ml_affiliate=None,
                 kabum_affiliate=None, ml_reader=None):
        self.base = Path(base)
        self.db = db
        self.shopee = shopee
        self.ml_affiliate = ml_affiliate
        self.kabum_affiliate = kabum_affiliate
        self.ml_reader = ml_reader
        self.source_price_max_age = max(
            60, int(os.getenv("GATE_SOURCE_PRICE_MAX_AGE_SECONDS", "300") or 300)
        )
        self.kabum_max_age = max(
            60, int(os.getenv("GATE_KABUM_FEED_MAX_AGE_SECONDS", "1800") or 1800)
        )
        self._init_db()
    def _init_db(self):
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS prepublication_gate (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          product TEXT NOT NULL,
          checked REAL NOT NULL,
          status TEXT NOT NULL,
          reason TEXT NOT NULL,
          source TEXT,
          old_price TEXT,
          new_price TEXT,
          details TEXT
        );
        CREATE INDEX IF NOT EXISTS gate_product_checked
          ON prepublication_gate(product, checked);
        CREATE INDEX IF NOT EXISTS gate_reason_checked
          ON prepublication_gate(reason, checked);
        """)
        cutoff = time.time() - 90 * 86400
        with self.db:
            self.db.execute(
                "DELETE FROM prepublication_gate WHERE checked<?", (cutoff,)
            )

    def _record(self, offer, status, reason, old_price="", new_price="", details=""):
        safe_details = str(details or "")[:300]
        with self.db:
            self.db.execute(
                """INSERT INTO prepublication_gate
                (product,checked,status,reason,source,old_price,new_price,details)
                VALUES (?,?,?,?,?,?,?,?)""",
                (
                    str(offer.get("product_id") or "UNKNOWN")[:160],
                    time.time(), status, reason,
                    str(offer.get("source") or "")[:80],
                    str(old_price or "")[:40], str(new_price or "")[:40],
                    safe_details,
                ),
            )

    def reject(self, offer, reason, message, *, discard=False, retry_after=300,
               old_price="", new_price="", details=""):
        self._record(offer, "BLOQUEADA", reason, old_price, new_price, details)
        raise GateReject(
            reason, message, discard=discard, retry_after=retry_after
        )
    @staticmethod
    def _age_seconds(offer):
        try:
            stamp = datetime.fromisoformat(str(offer.get("source_date") or ""))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds())
        except (TypeError, ValueError):
            return float("inf")

    @staticmethod
    def _copy_price_fields(target, fresh):
        for key in (
            "price", "price_condition", "price_from", "name", "api_image",
            "discount", "rating", "sales", "stock_status", "stock_confirmed",
        ):
            if key in fresh and fresh.get(key) not in (None, ""):
                target[key] = fresh[key]
        return target

    def _compare_price(self, original, current, *, strong=True):
        old_value, new_value = cents(original), cents(current)
        if not new_value:
            self.reject(
                current, "PRECO_NAO_CONFIRMADO",
                "Gate: preço atual não pôde ser confirmado.", discard=strong,
                old_price=original.get("price", ""),
            )
        if current.get("price_from"):
            self.reject(
                current, "PRECO_VARIANTE_AMBIGUO",
                "Gate: preço atual é uma faixa/a partir de; publicação bloqueada.",
                discard=strong, old_price=original.get("price", ""),
                new_price=current.get("price", ""),
            )
        if old_value and new_value > old_value:
            self.reject(
                current, "PRECO_AUMENTOU",
                "Gate: o preço aumentou desde a entrada na fila; oferta descartada.",
                discard=True, old_price=original.get("price", ""),
                new_price=current.get("price", ""),
            )
        return old_value, new_value

    def _check_product_identity(self, offer):
        found = product(str(offer.get("url") or ""))
        if not found or found[0] != offer.get("product_id") or found[1] != offer.get("store"):
            self.reject(
                offer, "PRODUTO_INCONSISTENTE",
                "Gate: URL e identidade do produto não coincidem.", discard=True,
            )
        return found
    def _validate_link(self, offer):
        if offer.get("kind") == "ml_manual_offer":
            from mercadolivre_manual import allowed_link
            if (not offer.get("manual_link_preserved")
                    or not allowed_link(offer.get("affiliate_url"))):
                self.reject(
                    offer, "LINK_INVALIDO",
                    "Gate: link manual Mercado Livre não é um destino autorizado.",
                    discard=True,
                )
            return
        if not offer.get("affiliate_generated"):
            self.reject(
                offer, "LINK_INVALIDO",
                "Gate: link de afiliado não foi gerado.", discard=False,
            )
        store = offer.get("store")
        link = offer.get("affiliate_url")
        if store == "Shopee":
            valid = valid_shopee_link(link)
        elif store == "Mercado Livre":
            valid = valid_ml_link(link)
        elif store == "KaBuM":
            valid = valid_kabum_link(link)
            if valid and self.kabum_affiliate and self.kabum_affiliate.publisher_id:
                query = parse_qs(urlparse(str(link)).query)
                publishers = query.get("a", []) + query.get("awinaffid", [])
                valid = self.kabum_affiliate.publisher_id in publishers
        else:
            valid = False
        if not valid:
            self.reject(
                offer, "LINK_INVALIDO",
                "Gate: link de afiliado não pertence a um fluxo validado.", discard=False,
            )

    def _check_duplicate(self, offer, channel):
        states = self.db.execute(
            "SELECT status FROM posts WHERE product=?", (offer["product_id"],)
        ).fetchall()
        if not states:
            return
        if offer.get("kind") == "coupon_alert":
            self.reject(
                offer, "DUPLICADA",
                "Gate: cupom já possui registro de publicação.", discard=True,
                retry_after=0,
            )
        if any(row[0] != "sent" for row in states):
            self.reject(
                offer, "DUPLICADA",
                "Gate: produto possui envio anterior não confirmado.", discard=True,
                retry_after=0,
            )
        if not Intelligence(self.db).can_repeat(offer, channel):
            self.reject(
                offer, "DUPLICADA",
                "Gate: republicação não atende a queda de preço e intervalo mínimos.",
                discard=True, retry_after=0,
            )
    def _fresh_ml(self, offer):
        try:
            fetched_age = time.time() - float(offer.get("auto_fetched_at") or 0)
        except (TypeError, ValueError):
            fetched_age = float("inf")
        if (0 <= fetched_age <= 60 and cents(offer)
                and offer.get("name") and (offer.get("api_image") or offer.get("image"))):
            return dict(offer)
        if self.ml_reader is None:
            self.reject(
                offer, "VALIDACAO_INDISPONIVEL",
                "Gate: leitor público Mercado Livre indisponível.", retry_after=300,
            )
        probe = dict(offer)
        for key in ("name", "price", "price_condition", "price_from",
                    "api_image", "image"):
            probe.pop(key, None)
        try:
            fresh = self.ml_reader.read(probe, blocking=True)
        except AffiliateError as exc:
            self.reject(
                offer, "VALIDACAO_INDISPONIVEL",
                "Gate: não foi possível confirmar o produto/preço atual do Mercado Livre.",
                retry_after=300, details=type(exc).__name__,
            )
        if not fresh:
            self.reject(
                offer, "VALIDACAO_INDISPONIVEL",
                "Gate: leitura atual do Mercado Livre não retornou dados.", retry_after=300,
            )
        if offer.get("kind") != "ml_manual_offer":
            identified = product(str(fresh.get("resolved_url") or fresh.get("url") or ""))
            if not identified or identified[0] != offer.get("product_id"):
                self.reject(
                    offer, "PRODUTO_INCONSISTENTE",
                    "Gate: Mercado Livre retornou outro produto.", discard=True,
                )
        return fresh

    def _fresh_kabum(self, offer):
        key = str(offer.get("product_id") or "")
        if not key.startswith("KaBuM:"):
            self.reject(
                offer, "PRODUTO_INCONSISTENTE",
                "Gate: ID KaBuM inválido.", discard=True,
            )
        product_id = key.split(":", 1)[1]
        path = self.base / "kabum_historico.sqlite3"
        try:
            db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
            row = db.execute(
                """SELECT name,price_cents,affiliate_url,image_url,in_stock,last_seen
                   FROM kabum_products WHERE product_id=?""",
                (product_id,),
            ).fetchone()
        except sqlite3.Error:
            row = None
        finally:
            try:
                db.close()
            except Exception:
                pass
        if not row:
            return None
        name, price_cents, affiliate, image, stock, last_seen = row
        if time.time() - float(last_seen or 0) > self.kabum_max_age:
            self.reject(
                offer, "VALIDACAO_INDISPONIVEL",
                "Gate: feed KaBuM está antigo demais para confirmar o preço.",
                retry_after=300,
            )
        fresh = dict(offer)
        fresh["name"] = str(name or offer.get("name") or "")[:160]
        fresh["price"] = f"{price_cents / 100:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
        fresh["price_from"] = False
        if str(image or "").startswith("https://"):
            fresh["api_image"] = image
        fresh["stock_status"] = str(stock or "")
        if str(stock or "").strip().lower() in {"out_of_stock", "unavailable", "0", "false", "no"}:
            self.reject(
                fresh, "SEM_ESTOQUE_COMPROVADO",
                "Gate: produto KaBuM explicitamente indisponível.", discard=True,
            )
        return fresh
    def _validate_kabum_coupon(self, offer):
        from awin_kabum import AwinKabumAPI
        api = AwinKabumAPI.from_env()
        if not api.enabled or self.kabum_affiliate is None:
            self.reject(
                offer, "VALIDACAO_INDISPONIVEL",
                "Gate: Offers API Awin indisponível para revalidar o cupom.", retry_after=300,
            )
        try:
            offers = api.offers()
        except Exception as exc:
            self.reject(
                offer, "VALIDACAO_INDISPONIVEL",
                "Gate: Offers API Awin não respondeu à revalidação.", retry_after=300,
                details=type(exc).__name__,
            )
        pid = str(offer.get("promotion_id") or "")
        current = next(
            (item for item in offers if str(item.get("promotionId") or "") == pid),
            None,
        )
        if current is None:
            self.reject(
                offer, "CUPOM_INATIVO",
                "Gate: cupom KaBuM não consta mais entre as ofertas oficiais ativas.",
                discard=True, retry_after=0,
            )
        try:
            fresh = cupons_kabum.alert_from_offer(
                current, self.kabum_affiliate
            )
        except AffiliateError:
            self.reject(
                offer, "CUPOM_EXPIRADO",
                "Gate: cupom KaBuM expirou ou deixou de atender aos critérios.",
                discard=True, retry_after=0,
            )
        return fresh

    def _validate_coupon(self, original, prepared):
        store = prepared.get("store")
        if store == "KaBuM":
            return self._validate_kabum_coupon(prepared)
        if store == "Mercado Livre":
            if self._age_seconds(original) > self.source_price_max_age:
                self.reject(
                    prepared, "CUPOM_NAO_REVALIDADO",
                    "Gate: lista Mercado Livre antiga demais para publicação sem nova confirmação.",
                    retry_after=300,
                )
            try:
                return ml_coupons.prepare_alert(prepared)
            except AffiliateError:
                self.reject(
                    prepared, "CUPOM_INVALIDO",
                    "Gate: lista de cupons Mercado Livre inválida.", retry_after=300,
                )
        if store == "Shopee":
            entries = prepared.get("entries") or []
            if (not entries or any(not isinstance(entry, dict) for entry in entries)
                    or any(
                        not valid_shopee_link(entry.get("affiliate_url"))
                        for entry in entries
                    )):
                self.reject(
                    prepared, "LINK_INVALIDO",
                    "Gate: cupom Shopee sem links de afiliado válidos.", retry_after=300,
                )
            if self._age_seconds(original) > self.source_price_max_age:
                self.reject(
                    prepared, "CUPOM_NAO_REVALIDADO",
                    "Gate: alerta Shopee antigo demais para publicação sem nova confirmação.",
                    retry_after=300,
                )
            return prepared
        self.reject(
            prepared, "CUPOM_INVALIDO",
            "Gate: tipo de cupom não reconhecido.", discard=True,
        )
    def validate(self, original, prepared, channel=""):
        """Retorna a versão final a publicar ou levanta GateReject."""
        original = dict(original)
        current = dict(prepared)
        is_coupon = current.get("kind") == "coupon_alert"

        if is_coupon:
            current = self._validate_coupon(original, current)
            self._check_duplicate(current, channel)
            self._record(current, "APROVADA", "CUPOM_REVALIDADO")
            current["prepublication_checked_at"] = time.time()
            return current

        if current.get("kind") != "ml_manual_offer":
            self._check_product_identity(current)
        self._validate_link(current)
        store = current.get("store")
        strong = False

        if store == "Shopee" and current.get("source") == "shopee_api":
            strong = True
            self._compare_price(original, current, strong=True)
        elif store == "Mercado Livre":
            if (current.get("kind") == "ml_manual_offer"
                    and not current.get("auto_fetched_at")
                    and self._age_seconds(original) <= self.source_price_max_age):
                self._compare_price(original, current, strong=False)
            else:
                fresh = self._fresh_ml(current)
                current = self._copy_price_fields(current, fresh)
                strong = True
                self._compare_price(original, current, strong=True)
        elif store == "KaBuM":
            fresh = self._fresh_kabum(current)
            if fresh is not None:
                current = self._copy_price_fields(current, fresh)
                strong = True
                self._compare_price(original, current, strong=True)
            elif self._age_seconds(original) > self.source_price_max_age:
                self.reject(
                    current, "PRECO_NAO_REVALIDADO",
                    "Gate: produto KaBuM fora do feed e preço da origem ficou antigo.",
                    retry_after=300,
                )
        elif store == "Shopee":
            if self._age_seconds(original) > self.source_price_max_age:
                self.reject(
                    current, "PRECO_NAO_REVALIDADO",
                    "Gate: preço Shopee de grupo ficou antigo e a API não comprova preço final com cupom/Pix.",
                    retry_after=300,
                )
        else:
            self.reject(
                current, "LOJA_NAO_INTEGRADA",
                "Gate: loja sem validação pré-publicação.", discard=True,
            )

        self._check_duplicate(current, channel)
        old_value, new_value = cents(original), cents(current)
        updated = bool(strong and old_value and new_value and new_value < old_value)
        reason = "PRECO_ATUALIZADO_MENOR" if updated else (
            "VALIDACAO_FORTE" if strong else "PRECO_ORIGEM_RECENTE"
        )
        self._record(
            current, "ATUALIZADA" if updated else "APROVADA", reason,
            original.get("price", ""), current.get("price", ""),
        )
        current["prepublication_checked_at"] = time.time()
        current["prepublication_validation"] = reason
        return current
