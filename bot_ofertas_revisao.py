"""Publicação Shopee com link gerado pela API de Afiliados. --simular não publica."""
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from ofertas_core import Ledger, caption, product
from shopee_afiliados import ShopeeAffiliate, AffiliateError
from mercadolivre_afiliados import MercadoLivreAffiliate, MercadoLivreSessionError
from mercadolivre_session_health import MLAffiliateSessionHealth, credential_fingerprint
from kabum_afiliados import KabumAffiliate
from amazon_afiliados import AmazonCreators
from cupons_shopee import prepare_alert, alert_caption, banner_path, send_alert
import cupons_mercadolivre as ml_coupons
import cupons_kabum as kabum_coupons
import mercadolivre_manual as ml_manual
import canal_espelho as mirror
from metricas_fontes import SourceMetrics
from prepublicacao import PrePublicationGate, GateReject
from mercadolivre_resiliencia import MLResilience
from telegram_api import TelegramSendError
from publicacao_oferta import send_offer as send, valid_price
from publisher_backoff import PublisherBackoff, offer_revision
from fila_ofertas_sqlite import CapturedOfferQueue
from configuracao import PublisherConfig, TelegramConfig

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / '.env', encoding='utf-8-sig')

def rows(captured_queue=None):
    own_queue = captured_queue is None
    queue = captured_queue or CapturedOfferQueue(BASE / 'publicacoes.sqlite3')
    try:
        config = PublisherConfig.from_env()
        queue.import_legacy_jsonl(
            BASE / 'fila_ofertas_v2.jsonl',
            max_age_minutes=config.offer_max_age_minutes,
            recovery_max_age_minutes=config.recovered_max_age_minutes,
        )
        yield from queue.pending()
    finally:
        if own_queue:
            queue.close()

def ordered_rows(intelligence=None, channel="", captured_queue=None):
    # Prioridade: grupos ao vivo > recuperadas > radar.
    # A pausa das recuperadas é aplicada no publicador; durante essa pausa o radar
    # continua podendo usar sua própria janela de 20 minutos.
    live_groups, recovered_groups, radar, latest = [], [], [], {}
    for offer in rows(captured_queue):
        if offer.get('source') == 'shopee_api':
            radar.append(offer)
        elif offer.get('kind') != 'coupon_alert' and offer.get('chat_id') is not None and offer.get('message_id') is not None:
            # A fila SQLite já mantém a revisão mais recente por mensagem;
            # esta camada também deduplica o mesmo produto vindo de fontes distintas.
            latest[(offer['chat_id'], offer['message_id'])] = offer
        elif offer.get('recovered'):
            recovered_groups.append(offer)
        else:
            live_groups.append(offer)
    for offer in latest.values():
        (recovered_groups if offer.get('recovered') else live_groups).append(offer)
    live_groups.sort(key=lambda o: str(o.get('source_date', '')), reverse=True)
    recovered_groups.sort(key=lambda o: str(o.get('source_date', '')), reverse=True)

    seen_products = set()
    def unique(bucket):
        for offer in bucket:
            key = offer.get('product_id')
            # O product_id dos cupons já representa a identidade do alerta.
            # Duplicatas append-only devem manter só a versão mais recente,
            # exatamente como produtos comuns, para não alternar revisões/backoff.
            if key:
                if key in seen_products:
                    continue
                seen_products.add(key)
            yield offer

    yield from unique(live_groups)
    yield from unique(recovered_groups)
    if intelligence is None:
        yield from unique(radar)
    else:
        yield from unique(intelligence.pending(channel))

def photo_path(offer):
    value = offer.get('image')
    if not value:
        return None
    path = (BASE / value).resolve()
    if not path.is_relative_to(BASE / 'imagens_ofertas') or not path.is_file():
        return None
    return path if 0 < path.stat().st_size <= 10_000_000 else None

def metric_reason_from_error(error):
    text = str(error or '').lower()
    if 'preço' in text:
        return 'PRECO_AUSENTE'
    if 'imagem' in text:
        return 'SEM_IMAGEM'
    if 'tema' in text:
        return 'TEMA_INVALIDO'
    if 'afiliad' in text or 'api' in text or 'link' in text:
        return 'AFILIADO_FALHOU'
    return 'PREPARACAO_FALHOU'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--simular', action='store_true')
    args = parser.parse_args()
    from contextlib import nullcontext
    from execucao_unica import instancia_unica
    with (nullcontext() if args.simular else instancia_unica(BASE / 'publicador.lock')):
        run_publisher(args, parser)


def run_publisher(args, parser):
    telegram = TelegramConfig.from_env()
    config = PublisherConfig.from_env()
    token, channel = telegram.token, telegram.channel
    if not args.simular and (not token or not channel):
        parser.error('Preencha TELEGRAM_TOKEN e TELEGRAM_CANAL no .env.')
    try:
        affiliate = ShopeeAffiliate.from_env()
    except AffiliateError as e:
        affiliate = None
        print("Shopee indisponível:", str(e), "| Mercado Livre e entrada manual continuam independentes.")
    try:
        ml_affiliate = MercadoLivreAffiliate.from_env()
    except AffiliateError as e:
        ml_affiliate = None
        print("Afiliados Mercado Livre indisponível:", str(e), "| links automáticos ML ficarão bloqueados.")
    try:
        kabum_affiliate = KabumAffiliate.from_env(BASE / 'kabum_historico.sqlite3')
    except AffiliateError as e:
        kabum_affiliate = None
        print("Afiliados KaBuM/Awin indisponível:", str(e), "| ofertas KaBuM ficarão aguardando.")
    try:
        amazon_affiliate = AmazonCreators.from_env()
    except AffiliateError as e:
        amazon_affiliate = None
        print("Amazon Creators API indisponível:", str(e), "| ofertas Amazon ficarão aguardando.")
    require_photo = config.require_image
    interval = config.radar_interval_seconds
    recovery_interval = config.recovered_interval_seconds
    recovery_max_age = config.recovered_max_age_minutes
    max_age = config.offer_max_age_minutes
    ledger = Ledger(BASE / 'publicacoes.sqlite3')
    if not args.simular:
        reservation_recovery = ledger.reconcile_reservations()
        if (reservation_recovery['moved_to_uncertain']
                or reservation_recovery['released_abandoned_reserved']):
            print(
                'Reservas recuperadas no início:',
                reservation_recovery['released_abandoned_reserved'], 'pré-envio liberadas;',
                reservation_recovery['moved_to_uncertain'], 'envios movidos para uncertain;',
                reservation_recovery['uncertain_total'], 'uncertain no total.',
            )
    metrics = SourceMetrics(BASE / 'publicacoes.sqlite3')
    captured_queue = CapturedOfferQueue(BASE / 'publicacoes.sqlite3')
    migration = captured_queue.import_legacy_jsonl(
        BASE / 'fila_ofertas_v2.jsonl',
        max_age_minutes=max_age,
        recovery_max_age_minutes=min(max_age, recovery_max_age),
    )
    if not args.simular and not migration['skipped']:
        print(
            'Migração JSONL -> SQLite:',
            migration['imported'], 'ofertas ativas importadas de',
            migration['scanned'], 'registros legados.'
        )
    backoff = PublisherBackoff(ledger.db, enabled=not args.simular)
    backoff.prune()
    ml_session_health = None
    ml_session_fingerprint = ''
    if ml_affiliate is not None and not args.simular:
        ml_session_health = MLAffiliateSessionHealth(ledger.db)
        ml_session_fingerprint = credential_fingerprint(
            ml_affiliate.cookie, ml_affiliate.csrf, ml_affiliate.tag
        )
    from inteligencia_ofertas import Intelligence
    intelligence = Intelligence(ledger.db)
    announced = set()
    prepared_coupons = {}
    gate_blocked = {}
    from mercadolivre_auto import AutoReader
    auto_reader = AutoReader()
    ml_resilience = MLResilience(ledger.db)
    gate = PrePublicationGate(
        BASE, ledger.db, shopee=affiliate, ml_affiliate=ml_affiliate,
        kabum_affiliate=kabum_affiliate, amazon_affiliate=amazon_affiliate,
        ml_reader=auto_reader,
    )
    print('Simulação: nada será publicado.' if args.simular else 'Publicador ativo: ofertas Shopee/Mercado Livre/KaBuM/Amazon e listas de cupons com Gate pré-publicação. Ctrl+C para parar.')
    try:
        while True:
            if not args.simular:
                delay = ledger.publication_delay(clock_id=3)
                if delay > 0:
                    time.sleep(min(delay, 1))
                    continue
            for offer in ordered_rows(intelligence, channel, captured_queue):
                key = offer.get('product_id')
                source_key = key
                revision = json.dumps(offer, sort_keys=True, ensure_ascii=False, default=str)
                revision_id = offer_revision(offer)
                if source_key in gate_blocked:
                    if gate_blocked[source_key] == revision:
                        continue
                    del gate_blocked[source_key]
                if (not args.simular and isinstance(source_key, str)
                        and backoff.remaining(source_key, revision_id) > 0):
                    continue
                original_offer = dict(offer)
                is_coupon = offer.get('kind') == 'coupon_alert'
                is_ml_coupon = is_coupon and offer.get('store') == 'Mercado Livre'
                is_kabum_coupon = is_coupon and offer.get('store') == 'KaBuM'
                is_ml_manual = offer.get('kind') == 'ml_manual_offer'
                is_ml_pending = offer.get('kind') == 'ml_offer_pending'
                is_ml_offer = offer.get('kind') == 'ml_offer'
                is_kabum_offer = offer.get('store') == 'KaBuM'
                is_amazon_offer = offer.get('store') == 'Amazon'
                is_mirror = offer.get('publish_mode') == 'mirror'
                is_recovered = bool(offer.get('recovered')) and offer.get('source') != 'shopee_api'
                if not isinstance(key, str):
                    continue
                if (is_ml_offer or is_ml_pending) and ml_affiliate is None:
                    marker = ('ml_afiliado_indisponivel', key)
                    if marker not in announced:
                        print('Oferta Mercado Livre captada, mas o gerador de afiliado está indisponível:', key)
                        announced.add(marker)
                    metrics.record_offer(offer, 'AGUARDANDO', 'AFILIADO_INDISPONIVEL')
                    gate_blocked[source_key] = revision
                    continue
                if is_kabum_offer and kabum_affiliate is None:
                    metrics.record_offer(offer, 'AGUARDANDO', 'AFILIADO_INDISPONIVEL')
                    gate_blocked[source_key] = revision
                    continue
                if is_amazon_offer and amazon_affiliate is None:
                    metrics.record_offer(offer, 'AGUARDANDO', 'AFILIADO_INDISPONIVEL')
                    gate_blocked[source_key] = revision
                    continue
                if not (is_ml_offer or is_ml_pending or is_kabum_offer or is_amazon_offer) and affiliate is None and not is_ml_coupon and not is_ml_manual:
                    metrics.record_offer(offer, 'AGUARDANDO', 'AFILIADO_INDISPONIVEL')
                    gate_blocked[source_key] = revision
                    continue
                if is_ml_manual:
                    if not ml_manual.trusted(offer.get('chat_id')):
                        metrics.record_offer(offer, 'REJEITADA', 'ORIGEM_NAO_AUTORIZADA')
                        gate_blocked[source_key] = revision
                        continue
                elif is_ml_pending:
                    if (not ml_manual.allowed_link(offer.get('url'))
                            or offer.get('product_id') != ml_manual.pending_key(offer['url'])):
                        metrics.record_offer(offer, 'REJEITADA', 'LINK_INVALIDO')
                        gate_blocked[source_key] = revision
                        continue
                    offer['store'] = 'Mercado Livre'
                elif not is_coupon:
                    checked = product(offer.get('url', ''))
                    if not checked or checked[0] != key or checked[2] != offer['url']:
                        metrics.record_offer(offer, 'REJEITADA', 'PRODUTO_INCONSISTENTE')
                        gate_blocked[source_key] = revision
                        continue
                    offer['store'] = checked[1]
                    if checked[1] not in ('Shopee', 'Mercado Livre', 'KaBuM', 'Amazon'):
                        if key not in announced:
                            print('Aguardando integração de afiliados da loja:', checked[1])
                            announced.add(key)
                        metrics.record_offer(offer, 'AGUARDANDO', 'LOJA_NAO_INTEGRADA')
                        gate_blocked[source_key] = revision
                        continue
                    if checked[1] == 'Mercado Livre' and not is_ml_offer:
                        metrics.record_offer(offer, 'REJEITADA', 'FLUXO_ML_INVALIDO')
                        gate_blocked[source_key] = revision
                        continue
                try:
                    age = (datetime.now(timezone.utc) - datetime.fromisoformat(offer['source_date'])).total_seconds()
                except (KeyError, TypeError, ValueError):
                    metrics.record_offer(offer, 'REJEITADA', 'DATA_INVALIDA')
                    gate_blocked[source_key] = revision
                    continue
                allowed_age = min(max_age, recovery_max_age) if is_recovered else max_age
                if age < -60 or age > allowed_age * 60:
                    metrics.record_offer(offer, 'REJEITADA', 'EXPIRADA')
                    gate_blocked[source_key] = revision
                    continue

                # Evita gastar API/navegador com itens que o ledger já bloquearia.
                # Amazon fica de fora: a Creators API pode comprovar um preço atual menor.
                if not is_coupon and not is_ml_pending and not is_amazon_offer:
                    states = ledger.db.execute(
                        'SELECT status FROM posts WHERE product=?', (key,)
                    ).fetchall()
                    if states and (
                        any(state[0] != 'sent' for state in states)
                        or not intelligence.can_repeat(offer, channel)
                    ):
                        if all(state[0] == 'sent' for state in states):
                            captured_queue.discard_product(source_key)
                        metrics.record_offer(offer, 'REJEITADA', 'DUPLICADA_PRE_FILTRO')
                        gate_blocked[source_key] = revision
                        continue

                if (is_ml_offer or is_ml_pending) and ml_session_health is not None:
                    can_try_affiliate, session_state = ml_session_health.can_try(
                        ml_session_fingerprint
                    )
                    if not can_try_affiliate:
                        marker = ('ml_afiliado_sessao', session_state.get('last_status'))
                        if marker not in announced:
                            print(
                                'Sessão de afiliados ML em circuit breaker; ofertas automáticas aguardando |',
                                'nova tentativa em', session_state['retry_after'], 's'
                            )
                            announced.add(marker)
                        metrics.record_offer(
                            offer, 'AGUARDANDO', 'ML_AFILIADO_SESSAO_BLOQUEADA'
                        )
                        continue

                if is_ml_manual or is_ml_offer or is_ml_pending:
                    if is_ml_manual and not args.simular and ledger.db.execute('SELECT 1 FROM posts WHERE product=?', (key,)).fetchone():
                        metrics.record_offer(offer, 'REJEITADA', 'DUPLICADA')
                        continue
                    needs_public_data = (is_ml_pending or (not is_mirror and (
                                         not offer.get('name') or not valid_price(offer)
                                         or (require_photo and not (offer.get('api_image') or photo_path(offer))))))
                    if needs_public_data:
                        can_try_ml, ml_state = ml_resilience.can_try(key)
                        if not can_try_ml:
                            reason = ml_state['reason']
                            marker = ('ml_resiliencia', key, reason, ml_state.get('source_reason'))
                            if marker not in announced:
                                print(
                                    'Leitura ML adiada pela resiliência:', key, '|', reason,
                                    '| causa:', ml_state.get('source_reason'),
                                    '| nova tentativa em', ml_state.get('retry_after'), 's'
                                )
                                announced.add(marker)
                            metrics.record_offer(offer, 'AGUARDANDO', reason)
                            continue
                        ml_read_key = key
                        try:
                            ready = auto_reader.read(offer, blocking=args.simular)
                            if ready is None:
                                marker = ('leitura_ml', key)
                                if marker not in announced:
                                    print('Buscando título, preço e imagem do link Mercado Livre:', key)
                                    announced.add(marker)
                                metrics.record_offer(offer, 'AGUARDANDO', 'LEITURA_ML')
                                continue
                            ml_resilience.success(ml_read_key)
                            offer = ready
                            offer.setdefault('auto_fetched_at', time.time())
                            key = offer.get('product_id')
                            is_ml_pending = offer.get('kind') == 'ml_offer_pending'
                            is_ml_offer = offer.get('kind') == 'ml_offer'
                            if not isinstance(key, str):
                                continue
                            if is_ml_pending:
                                metrics.record_offer(offer, 'AGUARDANDO', 'LEITURA_ML')
                                state = backoff.failure(
                                    source_key, revision_id, 'LEITURA_ML', base=300
                                )
                                print('Leitura automática ML ainda não normalizou o produto:', key,
                                      '| tentativa:', state['failures'],
                                      '| próxima em', state['delay'], 's')
                                continue
                            if is_ml_offer:
                                checked = product(offer.get('url', ''))
                                if (not checked or checked[0] != key or checked[1] != 'Mercado Livre'
                                        or checked[2] != offer['url']):
                                    metrics.record_offer(offer, 'REJEITADA', 'PRODUTO_INCONSISTENTE')
                                    state = backoff.failure(
                                        source_key, revision_id, 'PRODUTO_INCONSISTENTE', base=300
                                    )
                                    print('Leitura ML retornou produto inconsistente; nova validação adiada:', key,
                                          '| tentativa:', state['failures'],
                                          '| próxima em', state['delay'], 's')
                                    continue
                        except AffiliateError as error:
                            state = ml_resilience.failure(ml_read_key, error)
                            metrics.record_offer(offer, 'AGUARDANDO', state['reason'])
                            suffix = (
                                ' | QUARENTENA' if state['quarantined'] else ''
                            ) + (
                                ' | CIRCUIT BREAKER ABERTO' if state['circuit_opened'] else ''
                            )
                            print(
                                'Leitura automática ML pendente:', ml_read_key, '|', str(error),
                                '| tentativa:', state['failures'],
                                '| próxima em', state['retry_after'], 's' + suffix,
                            )
                            continue
                if not is_coupon and not is_mirror and not is_amazon_offer and not valid_price(offer):
                    marker = ('sem_preco', key)
                    if marker not in announced:
                        print('Aguardando preço explícito ou edição na origem:', key)
                        announced.add(marker)
                    metrics.record_offer(offer, 'AGUARDANDO', 'PRECO_AUSENTE')
                    gate_blocked[source_key] = revision
                    continue
                if args.simular and key in announced:
                    continue
                if is_coupon:
                    raw_key = key
                    try:
                        if raw_key not in prepared_coupons:
                            if is_ml_coupon:
                                prepared_coupons[raw_key] = ml_coupons.prepare_alert(offer)
                            elif is_kabum_coupon:
                                prepared_coupons[raw_key] = kabum_coupons.prepare_alert(kabum_affiliate, offer)
                            else:
                                prepared_coupons[raw_key] = prepare_alert(affiliate, offer)
                        offer = prepared_coupons[raw_key]
                        key = offer['product_id']
                    except AffiliateError as error:
                        reason = metric_reason_from_error(error)
                        metrics.record_offer(offer, 'AGUARDANDO', reason)
                        state = backoff.failure(raw_key, revision_id, reason, base=300)
                        print(str(error), '| tentativa:', state['failures'],
                              '| próxima em', state['delay'], 's')
                        continue
                    if args.simular and key in announced:
                        continue
                origin = ('radar' if offer.get('source') in ('shopee_api', 'kabum_feed', 'kabum_awin_coupon')
                          else ('recuperada' if is_recovered else 'telegram'))
                if not args.simular and is_recovered and ledger.publication_delay(clock_id=4) > 0:
                    continue
                if not args.simular and origin == 'radar' and ledger.publication_delay(clock_id=2) > 0:
                    continue
                day = None
                theme = offer.get('tema_radar')
                try:
                    if not is_coupon:
                        if is_ml_manual:
                            offer = ml_manual.prepare(offer)
                        elif is_ml_offer:
                            offer = ml_affiliate.prepare(offer)
                            if ml_session_health is not None:
                                ml_session_health.success(ml_session_fingerprint)
                        elif offer.get('store') == 'KaBuM':
                            offer = kabum_affiliate.prepare(offer)
                        elif offer.get('store') == 'Amazon':
                            offer = amazon_affiliate.prepare(offer)
                        else:
                            offer = affiliate.prepare(offer)
                        if not is_mirror and not valid_price(offer):
                            raise AffiliateError('Publicação bloqueada: preço ausente ou inválido após preparação.')
                    if theme:
                        offer['tema_radar'] = theme
                        from radar_shopee_continuo import pertence_ao_tema
                        if not pertence_ao_tema(theme, offer):
                            raise AffiliateError('Título atualizado fora do tema; oferta ignorada.')
                except MercadoLivreSessionError as e:
                    if day:
                        ledger.release(key, day)
                    if ml_affiliate is not None:
                        ml_affiliate.cache.clear()
                    session_state = (
                        ml_session_health.failure(
                            ml_session_fingerprint, e.status, str(e)
                        )
                        if ml_session_health is not None
                        else {'retry_after': 900, 'failures': 1, 'alert_sent': False}
                    )
                    metrics.record_offer(
                        offer, 'AGUARDANDO', 'ML_AFILIADO_SESSAO'
                    )
                    print(
                        'Sessão de afiliados Mercado Livre recusada | HTTP',
                        e.status, '| tentativa:', session_state['failures'],
                        '| circuit breaker:', session_state['retry_after'], 's',
                        '| alerta admin:',
                        'enviado' if session_state['alert_sent'] else 'não enviado',
                    )
                    continue
                except AffiliateError as e:
                    if day:
                        ledger.release(key, day)
                    metrics.record_offer(offer, 'REJEITADA', metric_reason_from_error(e))
                    print(str(e))
                    if origin == 'radar':
                        intelligence.discard(key)
                    reason = metric_reason_from_error(e)
                    state = backoff.failure(source_key, revision_id, reason, base=300)
                    print('Retentativa adiada:', source_key, '| tentativa:',
                          state['failures'], '| próxima em', state['delay'], 's')
                    continue

                try:
                    offer = gate.validate(original_offer, offer, channel)
                    key = offer['product_id']
                except GateReject as error:
                    state = 'REJEITADA' if error.discard else 'AGUARDANDO'
                    metrics.record_offer(offer, state, error.reason)
                    print(str(error), '| motivo:', error.reason)
                    if error.discard:
                        gate_blocked[source_key] = revision
                        if origin == 'radar':
                            intelligence.discard(key)
                    elif error.retry_after:
                        state = backoff.failure(
                            source_key, revision_id, error.reason,
                            base=error.retry_after,
                        )
                        print('Gate reagendado:', source_key, '| tentativa:',
                              state['failures'], '| próxima em', state['delay'], 's')
                    continue

                if not is_coupon and not is_mirror:
                    if offer.get('source') != 'kabum_feed':
                        offer['history_badge'] = intelligence.badge(offer, channel)
                    else:
                        offer['history_badge'] = str(offer.get('history_badge') or '')
                day = None if args.simular else ledger.reserve(key, offer=offer, channel=channel)
                if not args.simular and not day:
                    metrics.record_offer(offer, 'REJEITADA', 'DUPLICADA')
                    continue
                if is_coupon:
                    image = (ml_coupons.banner_path(BASE) if is_ml_coupon
                             else (kabum_coupons.banner_path(BASE) if is_kabum_coupon
                                   else banner_path(BASE)))
                else:
                    image = ((photo_path(offer) or offer.get('api_image')) if is_mirror
                             else (offer.get('api_image') or photo_path(offer)))
                if require_photo and not image and not is_coupon:
                    if key not in announced:
                        print('Aguardando imagem autorizada:', key)
                        announced.add(key)
                    if day:
                        ledger.release(key, day)
                    metrics.record_offer(offer, 'AGUARDANDO', 'SEM_IMAGEM')
                    state = backoff.failure(
                        source_key, revision_id, 'SEM_IMAGEM', base=60
                    )
                    print('Imagem ainda indisponível:', source_key, '| tentativa:',
                          state['failures'], '| próxima em', state['delay'], 's')
                    continue
                if args.simular:
                    if key not in announced:
                        preview = ((ml_coupons.alert_caption(offer) if is_ml_coupon
                                    else (kabum_coupons.alert_caption(offer) if is_kabum_coupon
                                          else alert_caption(offer))) if is_coupon
                                   else (mirror.render(offer.get('mirror_template'), offer.get('affiliate_url'))
                                         if is_mirror else caption(offer)))
                        if is_ml_coupon:
                            links = []
                        elif is_kabum_coupon:
                            links = [offer['affiliate_url']]
                        else:
                            links = ([e['affiliate_url'] for e in offer['entries']]
                                     if is_coupon else [offer['affiliate_url']])
                        print('\n', key, '\n', preview, '\n', '\n'.join(links), '\nImagem:', bool(image))
                        announced.add(key)
                    continue
                sender = (ml_coupons.send_alert if is_ml_coupon
                          else (kabum_coupons.send_alert if is_kabum_coupon
                                else (send_alert if is_coupon else send)))
                if not ledger.mark_sending(key, day):
                    metrics.record_offer(offer, 'AGUARDANDO', 'RESERVA_INVALIDA')
                    print('Reserva mudou antes do envio; publicação cancelada com segurança:', key)
                    continue
                try:
                    # Grava antes do envio: falha ou reinício não encurta a pausa.
                    if origin == 'radar':
                        ledger.mark_attempt(interval, clock_id=2)
                    elif is_recovered:
                        ledger.mark_attempt(recovery_interval, clock_id=4)
                    message_id, _ = sender(token, channel, offer, image)
                except TelegramSendError as error:
                    if error.kind == 'uncertain':
                        backoff.clear(source_key)
                        ledger.mark_uncertain(key, day)
                        metrics.record_offer(offer, 'AGUARDANDO', 'ENVIO_INCERTO')
                        print('Resposta Telegram incerta. Reserva marcada como uncertain:', key)
                        break

                    ledger.release(key, day)
                    if error.kind == 'permanent':
                        backoff.clear(source_key)
                        captured_queue.discard_product(source_key)
                        metrics.record_offer(offer, 'REJEITADA', 'TELEGRAM_4XX')
                        gate_blocked[source_key] = revision
                        if origin == 'radar':
                            intelligence.discard(key)
                        print('Telegram rejeitou permanentemente a oferta:', key,
                              '| HTTP/API', error.error_code, '|', error.description)
                        continue

                    retry = max(error.retry_after, 1)
                    if error.kind == 'rate_limit':
                        backoff.clear(source_key)
                        metrics.record_offer(offer, 'AGUARDANDO', 'TELEGRAM_429')
                        ledger.mark_attempt(retry, clock_id=3)
                        print('Telegram aplicou rate limit; fila pausada por', retry, 's.')
                        break
                    if error.kind == 'configuration':
                        backoff.clear(source_key)
                        metrics.record_offer(offer, 'AGUARDANDO', 'TELEGRAM_CONFIG')
                        ledger.mark_attempt(retry, clock_id=3)
                        print('Telegram recusou a configuração do bot/canal; nova tentativa em',
                              retry, 's | código', error.error_code)
                        break

                    metrics.record_offer(offer, 'AGUARDANDO', 'TELEGRAM_TRANSITORIO')
                    state = backoff.failure(
                        source_key, revision_id, 'TELEGRAM_TRANSITORIO',
                        base=retry,
                    )
                    print('Falha transitória do Telegram:', key, '| código',
                          error.error_code, '| tentativa:', state['failures'],
                          '| nova tentativa em', state['delay'], 's.')
                    continue
                except Exception:
                    backoff.clear(source_key)
                    ledger.mark_uncertain(key, day)
                    metrics.record_offer(offer, 'AGUARDANDO', 'ENVIO_INCERTO')
                    print('Envio com resultado incerto. Reserva marcada como uncertain:', key,
                          '(Confira o canal antes de liberar; detalhes sensíveis foram omitidos.)')
                    break
                ledger.finish(key, day, message_id, offer=offer, channel=channel)
                captured_queue.discard_product(source_key)
                backoff.clear(source_key)
                if offer.get('source') == 'kabum_awin_coupon':
                    intelligence.discard(key)
                metrics.record_offer(offer, 'PUBLICADA', published_message_id=message_id)
                print('Publicado:', key, '| origem:', origin, '| mensagem', message_id)
                break  # Lê novamente as filas e verifica a idade após a espera.
            if args.simular:
                break
            time.sleep(1)
    finally:
        captured_queue.close()
        auto_reader.close()

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Publicador encerrado.')