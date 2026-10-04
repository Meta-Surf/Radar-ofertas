"""Gate central de validação imediatamente antes da publicação."""
import json
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
from amazon_afiliados import valid_affiliate_url as valid_amazon_link
import cupons_kabum
import cupons_mercadolivre as ml_coupons
from configuracao import GateConfig
from revisao_publicacao import catalog_digest


class GateReject(AffiliateError):
    def __init__(self, reason, message, *, discard=False, retry_after=300):
        super().__init__(message)
        self.reason = str(reason)
        self.discard = bool(discard)
        self.retry_after = max(0, int(retry_after))


class PrePublicationGate:
    def __init__(self, base, db, shopee=None, ml_affiliate=None,
                 kabum_affiliate=None, amazon_affiliate=None, ml_reader=None):
        self.base = Path(base)
        self.db = db
        self.shopee = shopee
        self.ml_affiliate = ml_affiliate
        self.kabum_affiliate = kabum_affiliate
        self.amazon_affiliate = amazon_affiliate
        self.ml_reader = ml_reader
        config = GateConfig.from_env()
        self.source_price_max_age = config.source_price_max_age_seconds
        self.kabum_max_age = config.kabum_feed_max_age_seconds
        self._runtime_cache = {}
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

    def _cached_value(self, key, ttl, producer):
        now = time.monotonic()
        cached = self._runtime_cache.get(key)
        if cached and now - cached[0] <= max(0, float(ttl)):
            return cached[1]
        value = producer()
        self._runtime_cache[key] = (now, value)
        if len(self._runtime_cache) > 128:
            oldest = min(self._runtime_cache, key=lambda item: self._runtime_cache[item][0])
            self._runtime_cache.pop(oldest, None)
        return value

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
        elif store == "Amazon":
            valid = bool(
                self.amazon_affiliate
                and valid_amazon_link(link, self.amazon_affiliate.partner_tag)
            )
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
        from mercadolivre_auto import MLCommercialUnavailable, MLProductInconsistent, confirmed_stock, same_product
        if self.ml_reader is None:
            self.reject(
                offer, "VALIDACAO_INDISPONIVEL",
                "Gate: leitor público Mercado Livre indisponível.", retry_after=300,
            )
        probe = dict(offer)
        for key in ("name", "price", "price_condition", "price_from",
                    "api_image", "image"):
            probe.pop(key, None)
        if (offer.get('kind') == 'ml_offer' and offer.get('source') == 'telegram'
                and offer.get('original_url')):
            from mercadolivre_manual import allowed_link, pending_key
            origin = offer['original_url']
            if allowed_link(origin):
                # A URL canônica identifica o item, mas pode perder o caminho
                # de sessão/redirecionamento que permitiu a leitura original.
                # Refazer esse caminho sem herdar preço; identidade conferida abaixo.
                probe.update(kind='ml_offer_pending', url=origin,
                             product_id=pending_key(origin))
        try:
            fresh = self.ml_reader.read(probe, blocking=True)
        except MLCommercialUnavailable as error:
            if error.resolved_url and not same_product(offer['url'], error.resolved_url):
                self.reject(offer, 'PRODUTO_INCONSISTENTE',
                            'Gate: recusa comercial pertence a outro produto/variante.', discard=True, retry_after=0)
            self.reject(offer, 'SEM_ESTOQUE_COMPROVADO',
                        'Gate: produto Mercado Livre explicitamente indisponível.', discard=True, retry_after=0)
        except MLProductInconsistent:
            self.reject(offer, 'PRODUTO_INCONSISTENTE',
                        'Gate: leitura Mercado Livre diverge do produto/variante esperado.', discard=True, retry_after=0)
        except AffiliateError as exc:
            self.reject(
                offer, "VALIDACAO_INDISPONIVEL",
                "Gate: não foi possível confirmar o produto/preço atual do Mercado Livre.",
                retry_after=300, details=type(exc).__name__,
            )
        if not isinstance(fresh, dict) or not fresh:
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
            if fresh.get('stock_status') == 'out_of_stock':
                self.reject(offer, 'SEM_ESTOQUE_COMPROVADO',
                            'Gate: produto Mercado Livre explicitamente indisponível.', discard=True, retry_after=0)
            if ((fresh.get('stock_product_id') and fresh['stock_product_id'] != offer['product_id'])
                    or (fresh.get('stock_resolved_url') and not same_product(offer['url'], fresh['stock_resolved_url']))):
                self.reject(offer, 'PRODUTO_INCONSISTENTE',
                            'Gate: prova de estoque pertence a outro produto/variante.', discard=True, retry_after=0)
            if not confirmed_stock(fresh, offer['url']):
                self.reject(offer, 'VALIDACAO_INDISPONIVEL',
                            'Gate: falta prova de disponibilidade do mesmo produto Mercado Livre.', retry_after=300)
        return fresh

    def _fresh_kabum(self, offer):
        key = str(offer.get("product_id") or "")
        if not key.startswith("KaBuM:"):
            self.reject(
                offer, "PRODUTO_INCONSISTENTE",
                "Gate: ID KaBuM inválido.", discard=True,
            )
        product_id = key.split(":", 1)[1]
        self.kabum_revision = {'product_id': product_id, 'digest': None}
        path = self.base / "kabum_historico.sqlite3"
        try:
            db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
            db.row_factory = sqlite3.Row
            row = db.execute(
                """SELECT *
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
        self.kabum_revision['digest'] = catalog_digest(dict(row))
        name, price_cents, affiliate, image, stock, last_seen = (
            row[key] for key in ('name','price_cents','affiliate_url','image_url','in_stock','last_seen'))
        self.kabum_revision['expires'] = float(last_seen or 0) + self.kabum_max_age
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
    def _validate_kabum_coupon(self, original, offer):
        from awin_kabum import AwinKabumAPI
        api = AwinKabumAPI.from_env()
        if not api.enabled or self.kabum_affiliate is None:
            self.reject(
                offer, "VALIDACAO_INDISPONIVEL",
                "Gate: Offers API Awin indisponível para revalidar o cupom.", retry_after=300,
            )
        try:
            offers = self._cached_value(
                'awin-kabum-offers', 60, api.offers
            )
        except Exception as exc:
            self.reject(
                offer, "VALIDACAO_INDISPONIVEL",
                "Gate: Offers API Awin não respondeu à revalidação.", retry_after=300,
                details=type(exc).__name__,
            )
        try:
            persisted = cupons_kabum.alert_unit(original)
            if not isinstance(offers, list):
                raise ValueError('Lista Awin inválida')
            matching = [item for item in offers if isinstance(item, dict)
                        and str(item.get('promotionId') or '') == persisted['promotion_id']]
            units = [cupons_kabum.commercial_unit(item) for item in matching]
            if not units or any(unit != units[0] for unit in units):
                raise ValueError('Promoção ausente ou ambígua')
        except (TypeError, ValueError):
            self.reject(offer, 'CUPOM_VALIDADE_NAO_CONFIRMADA',
                        'Gate: unidade comercial KaBuM ausente, incompleta ou ambígua.', retry_after=300)
        if units[0] != persisted:
            self.reject(offer, 'REVISAO_COMERCIAL_ALTERADA',
                        'Gate: promoção mudou; aguardar nova revisão persistida pelo coletor.', retry_after=300)
        reason = cupons_kabum.period_reason(persisted, datetime.now(timezone.utc).timestamp())
        if reason:
            self.reject(offer, reason, 'Gate: cupom fora do período comercial nativo.',
                        discard=reason == 'CUPOM_EXPIRADO', retry_after=0 if reason == 'CUPOM_EXPIRADO' else 300)
        try:
            fresh = cupons_kabum.alert_from_offer(matching[0], self.kabum_affiliate)
            fresh = cupons_kabum.prepare_alert(self.kabum_affiliate, fresh)
        except AffiliateError:
            self.reject(
                offer, "CUPOM_VALIDADE_NAO_CONFIRMADA",
                "Gate: cupom KaBuM deixou de atender aos critérios.", retry_after=300,
            )
        return fresh

    def _validate_coupon(self, original, prepared):
        store = prepared.get("store")
        if store == "KaBuM":
            return self._validate_kabum_coupon(original, prepared)
        if store == "Mercado Livre":
            if self._age_seconds(original) > self.source_price_max_age:
                self.reject(
                    prepared, "CUPOM_NAO_REVALIDADO",
                    "Gate: lista Mercado Livre antiga demais para publicação sem nova confirmação.",
                    retry_after=300,
                )
            try:
                fresh = ml_coupons.prepare_alert(prepared)
            except AffiliateError:
                self.reject(
                    prepared, "CUPOM_INVALIDO",
                    "Gate: lista de cupons Mercado Livre inválida.", retry_after=300,
                )
            fresh, validity = ml_coupons.validate_deadlines(fresh, datetime.now(timezone.utc))
            if validity == 'expired':
                self.reject(fresh, 'CUPOM_EXPIRADO',
                    'Gate: prazo explícito da lista de cupons Mercado Livre já encerrou.',
                    discard=True, retry_after=0)
            if validity == 'unknown':
                self.reject(fresh, 'CUPOM_VALIDADE_NAO_CONFIRMADA',
                    'Gate: validade ou associação do prazo Mercado Livre não pôde ser confirmada.',
                    retry_after=300)
            return fresh
        if store == "Shopee":
            from cupons_shopee import alert_key, deadline_status
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
            now = datetime.now(timezone.utc)
            states = [deadline_status(entry.get('conditions', ''), now) for entry in entries]
            eligible = [entry for entry, state in zip(entries, states)
                        if state in ('active', 'unspecified')]
            if not eligible:
                if 'unknown' in states:
                    self.reject(prepared, 'CUPOM_VALIDADE_NAO_CONFIRMADA',
                        'Gate: prazo declarado do cupom Shopee não pôde ser confirmado.',
                        retry_after=300)
                self.reject(prepared, 'CUPOM_EXPIRADO',
                    'Gate: prazo explícito dos cupons Shopee já encerrou.',
                    discard=True, retry_after=0)
            if len(eligible) != len(entries):
                prepared = dict(prepared, entries=eligible,
                    product_id=alert_key(eligible, prepared['source_date']))
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

    def _validate_kabum_product_voucher(self, offer):
        from awin_kabum import AwinKabumAPI, product_voucher_metadata, product_voucher_status
        def period(voucher):
            status = product_voucher_status(voucher, datetime.now(timezone.utc))
            if status == 'expired':
                self.reject(offer, 'CUPOM_EXPIRADO', 'Gate: voucher do produto KaBuM expirou.',
                            discard=True, retry_after=0)
            if status == 'not_started':
                self.reject(offer, 'CUPOM_NAO_INICIADO', 'Gate: voucher do produto KaBuM ainda não iniciou.')
        try:
            selected = product_voucher_metadata(offer.get('kabum_voucher'), offer['product_id'])
            if selected['coupon'] != offer.get('coupon'):
                raise ValueError('Código não corresponde à promoção selecionada')
            period(selected)
        except (TypeError, ValueError):
            self.reject(offer, 'CUPOM_VALIDADE_NAO_CONFIRMADA',
                        'Gate: voucher do produto KaBuM sem proveniência comercial suficiente.')
        api = AwinKabumAPI.from_env()
        if not api.enabled:
            self.reject(offer, 'VALIDACAO_INDISPONIVEL', 'Gate: Offers API indisponível para validar voucher do produto.')
        try:
            offers = self._cached_value('awin-kabum-offers', 60, api.offers)
            if not isinstance(offers, list):
                raise ValueError('Resposta Offers inválida')
        except Exception as exc:
            self.reject(offer, 'VALIDACAO_INDISPONIVEL', 'Gate: Offers API não confirmou voucher do produto.',
                        details=type(exc).__name__)
        matching = [item for item in offers if isinstance(item, dict)
                    and str(item.get('promotionId') or '') == selected['promotion_id']]
        try:
            if len(matching) != 1 or not AwinKabumAPI._official_kabum_offer(matching[0]):
                raise ValueError('Promoção ausente ou ambígua')
            mapped = AwinKabumAPI.product_offer_map(api, matching)
            candidates = mapped.get(offer['product_id'], [])
            if len(candidates) != 1:
                raise ValueError('Promoção de outro produto')
            fresh = product_voucher_metadata(candidates[0], offer['product_id'])
            # Período é sempre recalculado, mesmo se a lista veio do cache.
            period(fresh)
            if fresh != selected:
                raise ValueError('Promoção comercial mudou; aguardar nova revisão')
        except (AttributeError, TypeError, ValueError):
            self.reject(offer, 'CUPOM_VALIDADE_NAO_CONFIRMADA',
                        'Gate: promoção/produto/código não correspondem à revisão selecionada.')
        return selected

    def _validate_shopee_product_period(self, original, prepared):
        from radar_shopee import product_offer_period, product_period_status
        if original.get('store') != 'Shopee' or original.get('source') != 'shopee_api':
            self.reject(prepared, 'OFERTA_VALIDADE_NAO_CONFIRMADA', 'Gate: origem Shopee não corresponde à seleção.')
        try:
            selected = product_offer_period(original.get('shopee_offer_period'), original['product_id'])
            refreshed = product_offer_period(prepared.get('shopee_offer_period'), prepared['product_id'])
        except (TypeError, ValueError):
            self.reject(prepared, 'OFERTA_VALIDADE_NAO_CONFIRMADA',
                        'Gate: produto Shopee sem período nativo associado à revisão selecionada.')
        if selected != refreshed:
            self.reject(prepared, 'REVISAO_COMERCIAL_ALTERADA',
                        'Gate: período Shopee mudou; aguardar nova revisão persistida.')
        status = product_period_status(refreshed, time.time())
        if status == 'expired':
            self.reject(prepared, 'OFERTA_EXPIRADA', 'Gate: período da oferta Shopee encerrou.',
                        discard=True, retry_after=0)
        if status == 'not_started':
            self.reject(prepared, 'OFERTA_NAO_INICIADA', 'Gate: período da oferta Shopee ainda não iniciou.')
        return refreshed

    def validate(self, original, prepared, channel="", *, selected=None):
        """Retorna a versão final a publicar ou levanta GateReject."""
        original = dict(original)
        current = dict(prepared)
        is_coupon = current.get("kind") == "coupon_alert"

        if is_coupon:
            current = self._validate_coupon(original, current)
            if cupons_kabum.is_native_alert(current):
                try:
                    current['_kabum_coupon_proof'] = cupons_kabum.build_proof(original, current, selected)
                except (KeyError, TypeError, ValueError):
                    self.reject(current, 'CUPOM_VALIDADE_NAO_CONFIRMADA',
                                'Gate: prova nativa do alerta KaBuM não pôde ser confirmada.', retry_after=300)
            if (current.get('source') == 'telegram'
                    and current.get('store') in ('Mercado Livre', 'Shopee')):
                from cupom_validade import build_proof
                try:
                    current['_coupon_deadline_proof'] = build_proof(original, current, selected)
                except (KeyError, TypeError, ValueError):
                    self.reject(current, 'CUPOM_VALIDADE_NAO_CONFIRMADA',
                        'Gate: prova do conteúdo final do cupom não pôde ser confirmada.', retry_after=300)
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
            current['_shopee_period_proof'] = self._validate_shopee_product_period(original, current)
            strong = True
            self._compare_price(original, current, strong=True)
        elif store == "Mercado Livre":
            if (current.get("kind") == "ml_manual_offer"
                    and not current.get("auto_fetched_at")
                    and self._age_seconds(original) <= self.source_price_max_age):
                self._compare_price(original, current, strong=False)
            else:
                fresh = self._fresh_ml(current)
                # Valida o preço lido antes de mesclar metadados da captura.
                self._compare_price(original, fresh, strong=True)
                current = self._copy_price_fields(current, fresh)
                from mercadolivre_auto import STOCK_FIELDS
                current.update({key: fresh[key] for key in STOCK_FIELDS if key in fresh})
                strong = True
        elif store == "KaBuM":
            if (current.get('source') == 'kabum_feed' and current.get('kind') == 'product_offer'
                    and (current.get('coupon') or current.get('kabum_voucher'))):
                current['_voucher_proof'] = self._validate_kabum_product_voucher(current)
            fresh = self._fresh_kabum(current)
            current['_catalog_revision'] = self.kabum_revision
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
        elif store == "Amazon":
            strong = True
            self._compare_price(original, current, strong=True)
            if current.get("stock_confirmed") is not True:
                self.reject(
                    current, "SEM_ESTOQUE_COMPROVADO",
                    "Gate: Amazon sem disponibilidade confirmada pela Creators API.",
                    discard=True,
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
