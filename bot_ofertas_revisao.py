"""Publicação Shopee com link gerado pela API de Afiliados. --simular não publica."""
import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from ofertas_core import Ledger, caption, product, price_info
from shopee_afiliados import ShopeeAffiliate, AffiliateError, valid_affiliate_url
from mercadolivre_afiliados import MercadoLivreAffiliate, valid_affiliate_url as valid_ml_affiliate_url
from kabum_afiliados import KabumAffiliate, valid_affiliate_url as valid_kabum_affiliate_url
from amazon_afiliados import AmazonCreators
from cupons_shopee import prepare_alert, alert_caption, banner_path, send_alert
import cupons_mercadolivre as ml_coupons
import cupons_kabum as kabum_coupons
import mercadolivre_manual as ml_manual
import canal_espelho as mirror
from metricas_fontes import SourceMetrics
from prepublicacao import PrePublicationGate, GateReject
from mercadolivre_resiliencia import MLResilience
from telegram_api import TelegramSendError, send_telegram

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / '.env', encoding='utf-8-sig')

def rows():
    for name in ('fila_ofertas_v2.jsonl', 'fila_shopee_api.jsonl'):
        path = BASE / name
        if not path.exists():
            continue
        # Fecha o arquivo antes de processar: permite substituir a fila no Windows.
        for line in path.read_text(encoding='utf-8').splitlines():
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    yield value
            except json.JSONDecodeError:
                continue

def ordered_rows(intelligence=None, channel=""):
    # Prioridade: grupos ao vivo > recuperadas > radar.
    # A pausa das recuperadas é aplicada no publicador; durante essa pausa o radar
    # continua podendo usar sua própria janela de 20 minutos.
    live_groups, recovered_groups, radar, latest = [], [], [], {}
    for offer in rows():
        if offer.get('source') == 'shopee_api':
            radar.append(offer)
        elif offer.get('kind') != 'coupon_alert' and offer.get('chat_id') is not None and offer.get('message_id') is not None:
            # A fila é append-only: a última captura substitui a versão anterior,
            # inclusive quando uma edição altera preço, mídia ou origem de recuperação.
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
            if offer.get('kind') != 'coupon_alert' and key:
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

def send(token, channel, offer, image):
    is_mirror = offer.get('publish_mode') == 'mirror'
    if not is_mirror and not valid_price(offer):
        raise AffiliateError('Publicação bloqueada: preço ausente ou inválido.')
    if offer.get('kind') == 'ml_manual_offer':
        offer = ml_manual.prepare(offer)
    elif not offer.get('affiliate_generated'):
        raise AffiliateError('Publicação bloqueada: falta link de afiliado gerado.')
    elif offer.get('store') == 'Mercado Livre':
        if not valid_ml_affiliate_url(offer.get('affiliate_url')):
            raise AffiliateError('Publicação bloqueada: link de afiliado Mercado Livre inválido.')
    elif offer.get('store') == 'KaBuM':
        if not valid_kabum_affiliate_url(offer.get('affiliate_url')):
            raise AffiliateError('Publicação bloqueada: link de afiliado KaBuM/Awin inválido.')
    elif not valid_affiliate_url(offer.get('affiliate_url')):
        raise AffiliateError('Publicação bloqueada: falta link gerado pela API de Afiliados.')

    rendered = (mirror.render(offer.get('mirror_template'), offer.get('affiliate_url'))
                if is_mirror else caption(offer))
    if mirror.visible_length(rendered) > 1024:
        image = None
    data = {'chat_id': channel, 'parse_mode': 'HTML'}
    if is_mirror:
        button = str(offer.get('mirror_button_text') or '').strip()
        if button:
            data['reply_markup'] = json.dumps({
                'inline_keyboard': [[{'text': button[:64], 'url': offer['affiliate_url']}]]
            })
    else:
        data['reply_markup'] = json.dumps({
            'inline_keyboard': [[{'text': '🛒 VER OFERTA', 'url': offer['affiliate_url']}]]
        })
    data['caption' if image else 'text'] = rendered
    message_id = send_telegram(
        requests, token, data, image=image, image_mime='image/jpeg'
    )
    return message_id, 0

def valid_price(offer):
    value = offer.get('price')
    if not isinstance(value, str):
        return False
    info = price_info('R$ ' + value)
    return bool(info and info['price'] == value and not info['price_condition'])


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
    token, channel = os.getenv('TELEGRAM_TOKEN'), os.getenv('TELEGRAM_CANAL')
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
    require_photo = os.getenv('EXIGIR_IMAGEM', '1') == '1'
    interval = 1200  # Intervalo exclusivo das publicações originadas no radar.
    recovery_interval = max(5, int(os.getenv('INTERVALO_RECUPERADAS', '30')))
    recovery_max_age = max(1, int(os.getenv('IDADE_MAXIMA_RECUPERADAS_MINUTOS', '45')))
    max_age = max(1, int(os.getenv('IDADE_MAXIMA_MINUTOS', '120')))
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
    from inteligencia_ofertas import Intelligence
    intelligence = Intelligence(ledger.db)
    announced = set()
    retry_at = {}
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
            for offer in ordered_rows(intelligence, channel):
                key = offer.get('product_id')
                source_key = key
                revision = json.dumps(offer, sort_keys=True, ensure_ascii=False, default=str)
                if source_key in gate_blocked:
                    if gate_blocked[source_key] == revision:
                        continue
                    del gate_blocked[source_key]
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
                if (is_ml_offer or is_ml_pending) and ml_affiliate is None:
                    marker = ('ml_afiliado_indisponivel', key)
                    if marker not in announced:
                        print('Oferta Mercado Livre captada, mas o gerador de afiliado está indisponível:', key)
                        announced.add(marker)
                    metrics.record_offer(offer, 'AGUARDANDO', 'AFILIADO_INDISPONIVEL')
                    continue
                if is_kabum_offer and kabum_affiliate is None:
                    metrics.record_offer(offer, 'AGUARDANDO', 'AFILIADO_INDISPONIVEL')
                    continue
                if is_amazon_offer and amazon_affiliate is None:
                    metrics.record_offer(offer, 'AGUARDANDO', 'AFILIADO_INDISPONIVEL')
                    continue
                if not (is_ml_offer or is_ml_pending or is_kabum_offer or is_amazon_offer) and affiliate is None and not is_ml_coupon and not is_ml_manual:
                    metrics.record_offer(offer, 'AGUARDANDO', 'AFILIADO_INDISPONIVEL')
                    continue
                if not isinstance(key, str):
                    continue
                if is_ml_manual:
                    if not ml_manual.trusted(offer.get('chat_id')):
                        metrics.record_offer(offer, 'REJEITADA', 'ORIGEM_NAO_AUTORIZADA')
                        continue
                elif is_ml_pending:
                    if (not ml_manual.allowed_link(offer.get('url'))
                            or offer.get('product_id') != ml_manual.pending_key(offer['url'])):
                        metrics.record_offer(offer, 'REJEITADA', 'LINK_INVALIDO')
                        continue
                    offer['store'] = 'Mercado Livre'
                elif not is_coupon:
                    checked = product(offer.get('url', ''))
                    if not checked or checked[0] != key or checked[2] != offer['url']:
                        metrics.record_offer(offer, 'REJEITADA', 'PRODUTO_INCONSISTENTE')
                        continue
                    offer['store'] = checked[1]
                    if checked[1] not in ('Shopee', 'Mercado Livre', 'KaBuM', 'Amazon'):
                        if key not in announced:
                            print('Aguardando integração de afiliados da loja:', checked[1])
                            announced.add(key)
                        metrics.record_offer(offer, 'AGUARDANDO', 'LOJA_NAO_INTEGRADA')
                        continue
                    if checked[1] == 'Mercado Livre' and not is_ml_offer:
                        metrics.record_offer(offer, 'REJEITADA', 'FLUXO_ML_INVALIDO')
                        continue
                try:
                    age = (datetime.now(timezone.utc) - datetime.fromisoformat(offer['source_date'])).total_seconds()
                except (KeyError, TypeError, ValueError):
                    metrics.record_offer(offer, 'REJEITADA', 'DATA_INVALIDA')
                    continue
                allowed_age = min(max_age, recovery_max_age) if is_recovered else max_age
                if age < -60 or age > allowed_age * 60:
                    metrics.record_offer(offer, 'REJEITADA', 'EXPIRADA')
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
                        metrics.record_offer(offer, 'REJEITADA', 'DUPLICADA_PRE_FILTRO')
                        gate_blocked[source_key] = revision
                        continue

                if is_ml_manual or is_ml_offer or is_ml_pending:
                    if time.monotonic() < retry_at.get(key, 0):
                        continue
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
                                print('Leitura automática ML ainda não normalizou o produto:', key)
                                retry_at[key] = time.monotonic() + 300
                                continue
                            if is_ml_offer:
                                checked = product(offer.get('url', ''))
                                if (not checked or checked[0] != key or checked[1] != 'Mercado Livre'
                                        or checked[2] != offer['url']):
                                    metrics.record_offer(offer, 'REJEITADA', 'PRODUTO_INCONSISTENTE')
                                    print('Leitura ML retornou produto inconsistente; publicação bloqueada:', key)
                                    retry_at[key] = time.monotonic() + 300
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
                    continue
                if time.monotonic() < retry_at.get(key, 0):
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
                        metrics.record_offer(offer, 'AGUARDANDO', metric_reason_from_error(error))
                        print(str(error))
                        retry_at[raw_key] = time.monotonic() + 300
                        continue
                    if time.monotonic() < retry_at.get(key, 0) or (args.simular and key in announced):
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
                except AffiliateError as e:
                    if day:
                        ledger.release(key, day)
                    metrics.record_offer(offer, 'REJEITADA', metric_reason_from_error(e))
                    print(str(e))
                    if origin == 'radar':
                        intelligence.discard(key)
                    retry_at[key] = time.monotonic() + 300
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
                        retry_at[key] = time.monotonic() + error.retry_after
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
                    retry_at[key] = time.monotonic() + 60
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
                        ledger.mark_uncertain(key, day)
                        metrics.record_offer(offer, 'AGUARDANDO', 'ENVIO_INCERTO')
                        print('Resposta Telegram incerta. Reserva marcada como uncertain:', key)
                        break

                    ledger.release(key, day)
                    if error.kind == 'permanent':
                        metrics.record_offer(offer, 'REJEITADA', 'TELEGRAM_4XX')
                        gate_blocked[source_key] = revision
                        if origin == 'radar':
                            intelligence.discard(key)
                        print('Telegram rejeitou permanentemente a oferta:', key,
                              '| HTTP/API', error.error_code, '|', error.description)
                        continue

                    retry = max(error.retry_after, 1)
                    retry_at[key] = time.monotonic() + retry
                    if error.kind == 'rate_limit':
                        metrics.record_offer(offer, 'AGUARDANDO', 'TELEGRAM_429')
                        ledger.mark_attempt(retry, clock_id=3)
                        print('Telegram aplicou rate limit; fila pausada por', retry, 's.')
                        break
                    if error.kind == 'configuration':
                        metrics.record_offer(offer, 'AGUARDANDO', 'TELEGRAM_CONFIG')
                        ledger.mark_attempt(retry, clock_id=3)
                        print('Telegram recusou a configuração do bot/canal; nova tentativa em',
                              retry, 's | código', error.error_code)
                        break

                    metrics.record_offer(offer, 'AGUARDANDO', 'TELEGRAM_TRANSITORIO')
                    print('Falha transitória do Telegram:', key, '| código',
                          error.error_code, '| nova tentativa em', retry, 's.')
                    continue
                except Exception:
                    ledger.mark_uncertain(key, day)
                    metrics.record_offer(offer, 'AGUARDANDO', 'ENVIO_INCERTO')
                    print('Envio com resultado incerto. Reserva marcada como uncertain:', key,
                          '(Confira o canal antes de liberar; detalhes sensíveis foram omitidos.)')
                    break
                ledger.finish(key, day, message_id, offer=offer, channel=channel)
                if offer.get('source') == 'kabum_awin_coupon':
                    intelligence.discard(key)
                metrics.record_offer(offer, 'PUBLICADA', published_message_id=message_id)
                print('Publicado:', key, '| origem:', origin, '| mensagem', message_id)
                break  # Lê novamente as filas e verifica a idade após a espera.
            if args.simular:
                break
            time.sleep(1)
    finally:
        auto_reader.close()

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Publicador encerrado.')