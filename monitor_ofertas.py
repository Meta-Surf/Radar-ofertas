"""Captura ofertas novas em grupos escolhidos. Use --listar para ver os IDs."""
import argparse
import asyncio
import json
import hashlib
import time
from collections import OrderedDict
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient, events
from ofertas_core import extract_links, resolve, product, price, price_info, coupon, coupon_page_links, safe_url
from cupons_shopee import build_alerts, coupon_entries
from cupons_mercadolivre import build_alert as build_ml_alert
import mercadolivre_manual as ml_manual
from shopee_afiliados import AffiliateError
import canal_espelho as mirror
import imagem_marca as image_branding
from metricas_fontes import SourceMetrics
from fila_ofertas_sqlite import CapturedOfferQueue
from configuracao import MonitorConfig, TelegramConfig, env_csv, env_int

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / '.env', encoding='utf-8-sig')
logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')

class CaptureRevisions:
    """Ignora atualizações sem mudança de texto, links ou mídia por uma hora."""
    def __init__(self, limit=2048, ttl=3600):
        self.limit, self.ttl = limit, ttl
        self.seen = OrderedDict()

    def identity(self, messages, chat_id):
        ordered = sorted(messages, key=lambda m: m.id)
        payload = [(m.id, m.raw_text or '', extract_links(m),
                    getattr(getattr(m, 'photo', None), 'id', None),
                    getattr(getattr(m, 'document', None), 'id', None)) for m in ordered]
        digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()
        return (chat_id, ordered[0].id), digest

    def unchanged(self, key, digest):
        previous = self.seen.get(key)
        return bool(previous and previous[0] == digest and time.monotonic() - previous[1] < self.ttl)

    def remember(self, key, digest):
        self.seen[key] = (digest, time.monotonic())
        self.seen.move_to_end(key)
        while len(self.seen) > self.limit:
            self.seen.popitem(last=False)


def bounded_env_int(name, default, minimum, maximum):
    """Compatibilidade: delega limites de inteiros ao parser central."""
    return env_int(name, default, minimum=minimum, maximum=maximum)


def configured_chat_values(name):
    """Valores de chat configurados no .env, normalizados para comparação."""
    return set(env_csv(name, lower=True))


def dialog_config_keys(dialog):
    """Chaves estáveis aceitas na configuração: ID numérico e @username."""
    keys = {str(dialog.id).lower()}
    username = str(getattr(dialog.entity, 'username', '') or '').strip().lower()
    if username:
        keys.add('@' + username)
    return keys


def dialog_is_configured(dialog, configured):
    return bool(dialog_config_keys(dialog) & set(configured))


def source_metric_context(chat_id, chat, messages):
    username = str(getattr(chat, 'username', '') or '').strip()
    name = str(getattr(chat, 'title', '') or getattr(chat, 'name', '') or username or chat_id)
    return {
        'source_name': name,
        'source_username': username or None,
        'source_date': messages[0].date.isoformat() if messages else None,
    }


def unresolved_metric_reason(reasons):
    text = ' | '.join(str(x or '') for x in reasons)
    if 'desconto.games' in text.lower() or 'redirecionamento' in text.lower():
        return 'REDIRECIONADOR_BLOQUEADO'
    return 'LINK_NAO_RESOLVIDO'


_TITLE_NON_PRODUCT = re.compile(r'https?://|R\$|[💵💰💸]', re.I)
_TITLE_CALL_TO_ACTION = re.compile(
    r'\b(?:cupom|link|compre|resgate)\b', re.I
)


def offer_title(text, *, exclude_call_to_action=True):
    """Escolhe uma linha de título sem confundir preço, link ou CTA com produto."""
    for raw in str(text or '').splitlines():
        line = re.sub(r'[*_`]', '', raw).strip()
        if not line or _TITLE_NON_PRODUCT.search(line):
            continue
        if exclude_call_to_action and _TITLE_CALL_TO_ACTION.search(line):
            continue
        return line
    return ''


def exclusive_coupon_message(text, messages, shopee_alerts=None, ml_alert=None):
    """Só cria publicação própria de cupom quando a mensagem é dedicada a cupons.

    Oferta de produto com preço/código de cupom continua sendo uma única oferta.
    """
    if not (shopee_alerts or ml_alert):
        return False
    if price_info(text):
        return False
    for message in messages:
        for url in extract_links(message):
            if product(url):
                return False
    return True


def load_monitor_state(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def save_monitor_state(path, state):
    """Estado operacional sem credenciais; gravação atômica para sobreviver a reinícios."""
    try:
        temp = path.with_suffix(path.suffix + '.tmp')
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(path)
    except OSError:
        logging.warning('Não foi possível atualizar o estado local do monitor.')


async def resolve_for_capture(url):
    """Até três tentativas só para falhas transitórias; mantém o diagnóstico."""
    import requests
    reason = ''
    for attempt in range(3):
        trace = []
        try:
            found = await asyncio.to_thread(resolve, url, trace.append)
            if found:
                return found, ''
            reason = '; '.join(trace[-4:]) or 'Destino sem produto identificável.'
            transient = any(re.match(r'HTTP (?:429|5\d\d)\b', item) for item in trace)
        except requests.RequestException as error:
            reason = 'Falha de rede: ' + type(error).__name__
            transient = True
        except Exception as error:
            return None, 'Falha ao interpretar o link: ' + type(error).__name__
        if not transient or attempt == 2:
            break
        await asyncio.sleep(attempt + 1)
    return None, reason


async def mirror_product_candidates(messages, excluded=None):
    """Resolve links do canal espelho e conserva somente produtos reais."""
    excluded = set(excluded or ())
    urls = list(dict.fromkeys(
        u for message in messages for u in extract_links(message)
        if u not in excluded and mirror.supported_store_url(u)
    ))
    found, ignored = [], []
    for url in urls:
        resolved, reason = await resolve_for_capture(url)
        if resolved and resolved[1] in ('Shopee', 'Mercado Livre', 'KaBuM', 'Amazon'):
            key, store, direct_url = resolved
            found.append({
                'source_url': url,
                'key': key,
                'store': store,
                'direct_url': direct_url,
                'kind': 'ml_offer' if store == 'Mercado Livre' else 'product_offer',
            })
        elif ml_manual.allowed_link(url):
            found.append({
                'source_url': url,
                'key': ml_manual.pending_key(url),
                'store': 'Mercado Livre',
                'direct_url': url,
                'kind': 'ml_offer_pending',
            })
        else:
            ignored.append((url, reason or 'Página da loja sem produto identificável.'))
    return found, ignored


async def edited_messages(client, event):
    """Reúne o álbum da edição sem misturar legendas de outros produtos."""
    message = event.message
    if not message.grouped_id:
        return [message]
    nearby = await client.get_messages(await event.get_input_chat(),
                                      ids=list(range(max(1, message.id - 10), message.id + 11)))
    album = {m.id: m for m in nearby if m and m.grouped_id == message.grouped_id}
    album[message.id] = message
    return [album[key] for key in sorted(album)]

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--listar', action='store_true')
    parser.add_argument('--diagnosticar', metavar='URL_TELEGRAM', help='Inspeciona uma mensagem, sem baixar fotos, escrever fila ou publicar.')
    args = parser.parse_args()
    telegram = TelegramConfig.from_env()
    config = MonitorConfig.from_env()
    api_id, api_hash = telegram.api_id, telegram.api_hash
    if not api_id.isdigit() or not api_hash:
        parser.error('Preencha TG_API_ID e TG_API_HASH no .env.')
    client = TelegramClient(str(BASE / 'monitor_ofertas'), int(api_id), api_hash, sequential_updates=True)
    captured_queue = None
    await client.start()
    try:
        dialogs = [d async for d in client.iter_dialogs() if d.is_group or d.is_channel]
        paused_requested = set(config.paused_chats)
        paused_chat_ids = {d.id for d in dialogs if dialog_is_configured(d, paused_requested)}
        if args.listar:
            for d in dialogs:
                print(d.id, d.name)
            return
        requested = list(config.chats)
        mirror_requested = mirror.configured_chats()
        for value in mirror_requested:
            if value not in requested:
                requested.append(value)
        if ml_manual.chat_id() and ml_manual.chat_id() not in requested:
            if any(str(d.id) == ml_manual.chat_id() for d in dialogs):
                requested.append(ml_manual.chat_id())
            else:
                print('Grupo manual ML não encontrado nesta conta; os demais grupos continuam ativos:', ml_manual.chat_id())
        if not requested:
            parser.error('Preencha TG_CHATS com IDs negativos ou @nomes da lista.')
        selected = []
        selected_dialogs = {}
        mirror_chat_ids = set()
        missing = []
        for value in requested:
            match = next((d for d in dialogs if str(d.id) == value or
                          '@' + str(getattr(d.entity, 'username', '')).lower() == value.lower()), None)
            if not match:
                missing.append(value)
                print('Chat configurado não encontrado; ignorando e mantendo os demais:', value)
                continue
            if dialog_is_configured(match, paused_requested):
                print('Chat pausado pela configuração; ignorando:', match.id, match.name)
                continue
            if str(match.id) == telegram.channel or ('@' + str(getattr(match.entity, 'username', '')).lower()) == telegram.channel.lower():
                parser.error('Remova o canal de destino de TG_CHATS para evitar ciclos.')
            selected.append(match.id)
            selected_dialogs[match.id] = match
            if dialog_is_configured(match, {x.lower() for x in mirror_requested}):
                mirror_chat_ids.add(match.id)
        if not selected:
            parser.error('Nenhum chat válido de TG_CHATS foi encontrado nesta conta. Use --listar para conferir os IDs.')
        allowed_media = set(config.media_chats)
        allowed_media.update(str(chat_id) for chat_id in mirror_chat_ids)
        # Canais com rebranding precisam da foto original para gerar a versão limpa.
        allowed_media.update(image_branding.configured_chats())
        if ml_manual.chat_id():
            allowed_media.add(ml_manual.chat_id())
        allowed_media.difference_update(str(chat_id) for chat_id in paused_chat_ids)

        publisher_max_age = config.offer_max_age_minutes
        recovery_publish_age = config.recovered_max_age_minutes
        recovery_minutes = config.recovery_minutes
        recovery_limit = config.recovery_limit
        recovery_state_path = BASE / 'monitor_recuperacao.json'
        recovery_state = load_monitor_state(recovery_state_path)
        metrics = SourceMetrics(BASE / 'publicacoes.sqlite3')

        if args.diagnosticar:
            match = re.fullmatch(r'https://t\.me/([A-Za-z0-9_]+)/(\d+)/?', args.diagnosticar)
            if not match:
                parser.error('Use um link como https://t.me/nomecanal/12345')
            dialog = next((d for d in dialogs if (getattr(d.entity, 'username', '') or '').lower() == match[1].lower()), None)
            if not dialog:
                parser.error('Canal não encontrado entre os chats da sua conta.')
            print('Canal:', dialog.name, '| ID:', dialog.id)
            print('Está em TG_CHATS:', dialog.id in selected)
            print('Pausado:', dialog.id in paused_chat_ids)
            print('Imagens autorizadas em TG_MEDIA_CHATS:', str(dialog.id) in allowed_media)
            print('Modo espelho:', dialog.id in mirror_chat_ids)
            print('Rebranding visual:', dialog.id not in paused_chat_ids and image_branding.enabled(dialog.id))
            message = await client.get_messages(dialog.entity, ids=int(match[2]))
            if not message:
                print('Mensagem não encontrada ou indisponível para sua conta.')
                return
            print('Data da mensagem:', message.date.isoformat())
            if getattr(dialog.entity, 'noforwards', False) or getattr(message, 'noforwards', False):
                print('Conteúdo protegido: o monitor não captura essa publicação.')
                return
            raw = message.raw_text or ''
            print('Preço identificado:', price(raw))
            print('Condição do preço:', (price_info(raw) or {}).get('price_condition') or 'ausente')
            print('Código de cupom explícito:', coupon(raw) or 'ausente')
            print('Foto nesta mensagem:', bool(message.photo), '| pertence a álbum:', bool(message.grouped_id))
            ml_alert = ml_manual.build_coupon([message], dialog.id)
            if ml_alert:
                from cupons_mercadolivre import alert_caption, visible_length
                print('Lista Mercado Livre:', len(ml_alert['entries']), 'cupons; links removidos.')
                print('Caracteres:', visible_length(alert_caption(ml_alert)))
                print('Diagnóstico encerrado. Nada foi publicado ou colocado na fila.')
                return
            if ml_manual.trusted(dialog.id):
                try:
                    row = ml_manual.build_offer([message], dialog.id)
                    print('Entrada manual ML: link preservado; preço:', row['price'] or 'ausente/ambíguo')
                except AffiliateError as error:
                    print(str(error))
                print('Diagnóstico encerrado. Nada foi publicado ou colocado na fila.')
                return
            entries = coupon_entries([message])
            print('Links identificados para alerta de cupons:', len(entries))
            excluded = coupon_page_links(raw) | {e['url'] for e in entries}
            for i, url in enumerate(extract_links(message), 1):
                if url in excluded:
                    print('Link', i, ': candidato a alerta de cupons; use cupons_shopee.py --testar para converter sem publicar.')
                elif not safe_url(url):
                    print('Link', i, ': fora das lojas reconhecidas.')
                else:
                    print('Analisando link', i)
                    try:
                        p = await asyncio.to_thread(resolve, url, print)
                        if p:
                            print('Produto:', p[0])
                        elif dialog.id in mirror_chat_ids and mirror.supported_store_url(url):
                            print('Página sem produto: será removida da publicação no modo espelho.')
                        else:
                            print('Produto: não identificado')
                    except Exception as e:
                        print('Falha de rede ou formato:', type(e).__name__)
            print('Diagnóstico encerrado. Nada foi publicado ou colocado na fila.')
            return

        captured_queue = CapturedOfferQueue(BASE / 'publicacoes.sqlite3')
        migration = captured_queue.import_legacy_jsonl(
            BASE / 'fila_ofertas_v2.jsonl',
            max_age_minutes=publisher_max_age,
            recovery_max_age_minutes=recovery_publish_age,
        )
        if not migration['skipped']:
            print(
                'Migração JSONL -> SQLite:',
                migration['imported'], 'ofertas ativas importadas de',
                migration['scanned'], 'registros legados.'
            )

        media_dir = BASE / 'imagens_ofertas'
        media_dir.mkdir(exist_ok=True)

        async def save_offer_photo(photo, chat_id):
            """Baixa foto autorizada e aplica rebranding quando a origem exigir."""
            target = media_dir / f'{chat_id}_{photo.id}.jpg'
            try:
                downloaded = await client.download_media(photo, file=str(target))
                if not downloaded or not target.exists() or not (0 < target.stat().st_size <= 10_000_000):
                    if target.exists():
                        target.unlink()
                    return None
                if image_branding.enabled(chat_id):
                    try:
                        await asyncio.to_thread(image_branding.apply, target)
                        print('Imagem com rebranding aplicado:', chat_id, photo.id)
                    except Exception as error:
                        # Nunca publica a arte original quando esse canal exige limpeza.
                        if target.exists():
                            target.unlink()
                        logging.warning(
                            'Rebranding visual falhou no chat %s, foto %s: %s',
                            chat_id, photo.id, type(error).__name__,
                        )
                        return None
                return str(target.relative_to(BASE))
            except Exception:
                if target.exists():
                    try:
                        target.unlink()
                    except OSError:
                        pass
                logging.warning('Imagem indisponível na mensagem %s.', photo.id)
                return None

        revisions = CaptureRevisions()
        capture_lock = asyncio.Lock()

        def queue_rows(rows):
            return captured_queue.replace_capture(
                rows,
                max_age_minutes=publisher_max_age,
                recovery_max_age_minutes=recovery_publish_age,
            )

        async def capture_once(messages, chat_id, chat, digest=None, recovered=False):
            metric_id = min(m.id for m in messages)
            metric_meta = source_metric_context(chat_id, chat, messages)
            def mark_metric(status, reason='', **extra):
                metrics.record(chat_id, metric_id, status, reason,
                               recovered=recovered, **metric_meta, **extra)
            mark_metric('RECEBIDA')
            if not chat or getattr(chat, 'noforwards', False) or any(getattr(m, 'noforwards', False) for m in messages):
                mark_metric('REJEITADA', 'CONTEUDO_PROTEGIDO')
                print('Ignorada: conteúdo protegido ou chat indisponível.', chat_id)
                return
            text = '\n'.join(m.raw_text or '' for m in messages)
            ml_alert = ml_manual.build_coupon(messages, chat_id)
            alerts = build_alerts(messages, chat_id)
            coupon_only = exclusive_coupon_message(text, messages, alerts, ml_alert)
            if coupon_only:
                if ml_alert:
                    ml_alert = dict(
                        ml_alert, capture_digest=digest,
                        recovered=recovered, **metric_meta
                    )
                    queue_rows([ml_alert])
                    metrics.record_offer(ml_alert, 'CAPTADA')
                    print('Lista exclusiva de cupons Mercado Livre captada:', len(ml_alert['entries']), '| uma publicação, sem links de terceiros.')
                    return
                if alerts:
                    alerts = [
                        dict(
                            alert, capture_digest=digest,
                            recovered=recovered, **metric_meta
                        )
                        for alert in alerts
                    ]
                    queue_rows(alerts)
                    for alert in alerts:
                        metrics.record_offer(alert, 'CAPTADA')
                    print('Mensagem exclusiva de cupons Shopee captada:', sum(len(a['entries']) for a in alerts), '| links aguardando conversão.')
                    return
            if ml_manual.trusted(chat_id):
                try:
                    row = ml_manual.build_offer(messages, chat_id)
                except AffiliateError as error:
                    mark_metric('REJEITADA', 'OFERTA_INVALIDA')
                    print(str(error), '| mensagem', messages[0].id)
                    return
                photo = next((m for m in messages if m.photo), None)
                if photo:
                    downloaded_image = await save_offer_photo(photo, chat_id)
                    if downloaded_image:
                        row['image'] = downloaded_image
                row.update(metric_meta)
                row['capture_digest'] = digest
                row['recovered'] = recovered
                queue_rows([row])
                metrics.record_offer(row, 'CAPTADA')
                print('Oferta manual Mercado Livre captada:', row['product_id'], '| preço:', row['price'] or 'aguardando leitura automática do link')
                return
            # Se havia código/link de cupom junto de uma oferta de produto, ele não
            # gera uma segunda publicação. O texto/código permanece na oferta.
            excluded = coupon_page_links(text) | {e['url'] for a in alerts for e in a['entries']}
            if chat_id in mirror_chat_ids:
                candidates, ignored_links = await mirror_product_candidates(messages, excluded)
                # Em oferta com preço, um link pode ter sido classificado como
                # "cupom" pelo texto promocional; reavalia todos os links da loja.
                if not candidates and price_info(text):
                    candidates, ignored_links = await mirror_product_candidates(messages, set())
                for ignored_url, ignored_reason in ignored_links:
                    print('Canal espelho descartou página sem produto:', ignored_url, '|', ignored_reason)
                if len(candidates) != 1:
                    if candidates:
                        mark_metric('REJEITADA', 'MULTIPLOS_PRODUTOS')
                        print('Canal espelho ignorado: publicação contém mais de um produto de loja.', chat_id, messages[0].id)
                    else:
                        mark_metric('REJEITADA', 'SEM_PRODUTO')
                        print('Canal espelho ignorado: nenhum link de produto de loja identificado.', chat_id, messages[0].id)
                    return
                candidate = candidates[0]
                source_url = candidate['source_url']
                key, store, direct_url, kind = (
                    candidate['key'], candidate['store'],
                    candidate['direct_url'], candidate['kind']
                )
                try:
                    mirror_template, mirror_button = mirror.build_template(messages, [source_url])
                except AffiliateError as error:
                    mark_metric('REJEITADA', 'OFERTA_INVALIDA')
                    print(str(error), '| mensagem', messages[0].id)
                    return
                image = None
                photo = next((m for m in messages if m.photo), None)
                if photo:
                    image = await save_offer_photo(photo, chat_id)
                captured_price = price_info(text) or {}
                title = offer_title(text, exclude_call_to_action=False)
                row = {'product_id': key, 'store': store, 'url': direct_url, 'source': 'telegram',
                       'kind': kind, 'publish_mode': 'mirror',
                       'mirror_template': mirror_template, 'mirror_button_text': mirror_button,
                       'mirror_source_url': source_url,
                       'name': title[:160], 'price': captured_price.get('price'),
                       'price_condition': captured_price.get('price_condition', ''),
                       'price_from': captured_price.get('price_from', False),
                       'coupon': coupon(text), 'image': image,
                       'chat_id': chat_id, 'message_id': min(m.id for m in messages),
                       'captured_at': datetime.now(timezone.utc).isoformat(),
                       'source_date': messages[0].date.isoformat(),
                       'capture_digest': digest, 'recovered': recovered, **metric_meta}
                queue_rows([row])
                metrics.record_offer(row, 'CAPTADA')
                print('Oferta espelho captada:', key, '| loja:', store, '| imagem:', bool(image))
                return
            urls = list(dict.fromkeys(u for m in messages for u in extract_links(m) if u not in excluded and safe_url(u)))
            products = {}
            pending_ml = {}
            unresolved = []
            for url in urls:
                p, reason = await resolve_for_capture(url)
                if p:
                    products[p[0]] = p
                elif ml_manual.allowed_link(url):
                    # Links meli.la podem depender de JS, sessão ou página intermediária.
                    # Não perde a oferta: envia à fila para o leitor ML com navegador resolver.
                    pending_ml[url] = reason or 'Aguardando resolução automática do Mercado Livre.'
                else:
                    unresolved.append(reason)
            # Um segundo link não resolvido fora do ML pode esconder outro produto.
            if unresolved:
                mark_metric('REJEITADA', unresolved_metric_reason(unresolved))
                print('Ignorada: não foi possível resolver todos os links do produto.',
                      chat_id, messages[0].id, '|', ' | '.join(dict.fromkeys(unresolved)))
                return
            # Não associa uma única imagem/preço a vários produtos diferentes.
            if len(products) + len(pending_ml) != 1:
                if products or pending_ml:
                    mark_metric('REJEITADA', 'MULTIPLOS_PRODUTOS')
                    print('Ignorada: mensagem contém vários produtos ou links ambíguos.')
                elif urls:
                    mark_metric('REJEITADA', 'LINK_NAO_RESOLVIDO')
                    print('Ignorada: não foi possível resolver o link do produto.', chat_id, messages[0].id)
                else:
                    mark_metric('REJEITADA', 'SEM_PRODUTO')
                return
            if pending_ml:
                direct_url = next(iter(pending_ml))
                key, store = ml_manual.pending_key(direct_url), 'Mercado Livre'
                kind = 'ml_offer_pending'
                print('Oferta Mercado Livre enfileirada para resolução automática:',
                      key, '| mensagem', messages[0].id)
            else:
                key, store, direct_url = next(iter(products.values()))
                kind = 'ml_offer' if store == 'Mercado Livre' else 'product_offer'
            if store not in ('Shopee', 'Mercado Livre', 'KaBuM', 'Amazon'):
                mark_metric('REJEITADA', 'LOJA_NAO_SUPORTADA', store=store)
                return
            image = None
            if str(chat_id) in allowed_media:
                photo = next((m for m in messages if m.photo), None)
                if photo:
                    image = await save_offer_photo(photo, chat_id)
            captured_price = price_info(text) or {}
            title = offer_title(text)
            row = {'product_id': key, 'store': store, 'url': direct_url, 'source': 'telegram',
                   'kind': kind,
                   'name': title[:160],
                   'price': captured_price.get('price'),
                   'price_condition': captured_price.get('price_condition', ''),
                   'price_from': captured_price.get('price_from', False),
                   'coupon': coupon(text), 'image': image,
                   'chat_id': chat_id, 'message_id': min(m.id for m in messages),
                   'captured_at': datetime.now(timezone.utc).isoformat(),
                   'source_date': messages[0].date.isoformat(),
                   'capture_digest': digest,
                   'recovered': recovered, **metric_meta}
            queue_rows([row])
            metrics.record_offer(row, 'CAPTADA')
            print('Oferta captada:', key, '| preço:', row['price'] or 'não identificado/ambíguo',
                  '| condição:', row['price_condition'] or 'nenhuma',
                  '| imagem:', bool(image), '| cupom:', row['coupon'] or 'não identificado')

        def remember_monitor_position(chat_id, messages):
            if not messages:
                return
            key = str(chat_id)
            previous = recovery_state.get(key) if isinstance(recovery_state.get(key), dict) else {}
            last_id = max(int(previous.get('last_message_id', 0) or 0),
                          max(m.id for m in messages))
            activity = max((getattr(m, 'edit_date', None) or m.date for m in messages),
                           default=datetime.now(timezone.utc))
            recovery_state[key] = {
                'last_message_id': last_id,
                'last_activity_at': activity.isoformat(),
                'checked_at': datetime.now(timezone.utc).isoformat(),
            }
            save_monitor_state(recovery_state_path, recovery_state)

        async def capture(messages, chat_id, chat, recovered=False):
            async with capture_lock:
                key, digest = revisions.identity(messages, chat_id)
                if revisions.unchanged(key, digest):
                    remember_monitor_position(chat_id, messages)
                    return
                await capture_once(messages, chat_id, chat, digest=digest, recovered=recovered)
                revisions.remember(key, digest)
                remember_monitor_position(chat_id, messages)

        async def recover_recent_messages():
            if recovery_minutes <= 0:
                print('Recuperação inicial de mensagens: desativada por TG_RECUPERAR_MINUTOS=0.')
                return
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=recovery_minutes)
            queued = captured_queue.revision_digests()
            scanned = recovered = already_queued = failures = 0
            print(f'Recuperando mensagens dos últimos {recovery_minutes} minutos '
                  f'(até {recovery_limit} por chat)...')
            for chat_id in dict.fromkeys(selected):
                dialog = selected_dialogs.get(chat_id)
                if not dialog:
                    continue
                recent = []
                try:
                    async for message in client.iter_messages(dialog.entity, limit=recovery_limit):
                        if not message:
                            continue
                        stamp = getattr(message, 'date', None)
                        if stamp and stamp < cutoff:
                            break
                        recent.append(message)
                except Exception as error:
                    failures += 1
                    print('Recuperação inicial: falha ao consultar chat', chat_id,
                          '|', type(error).__name__)
                    continue

                batches = OrderedDict()
                for message in sorted(recent, key=lambda item: item.id):
                    token = (('album', message.grouped_id) if getattr(message, 'grouped_id', None)
                             else ('single', message.id))
                    batches.setdefault(token, []).append(message)

                scanned += len(batches)
                for messages in batches.values():
                    identity, digest = revisions.identity(messages, chat_id)
                    queue_key = (str(chat_id), identity[1])
                    if queued.get(queue_key) == digest:
                        revisions.remember(identity, digest)
                        remember_monitor_position(chat_id, messages)
                        already_queued += 1
                        continue
                    try:
                        await capture(messages, chat_id, dialog.entity, recovered=True)
                        recovered += 1
                    except Exception as error:
                        failures += 1
                        print('Recuperação inicial: falha ao reprocessar mensagem',
                              identity[1], 'do chat', chat_id, '|', type(error).__name__)

            print('Recuperação inicial concluída:',
                  recovered, 'lotes reprocessados,',
                  already_queued, 'já presentes na fila,',
                  scanned, 'lotes examinados' +
                  (f', {failures} falhas isoladas.' if failures else '.'))

        @client.on(events.NewMessage(chats=selected))
        async def receive(event):
            if not event.message.grouped_id:
                await capture([event.message], event.chat_id, await event.get_chat())

        @client.on(events.Album(chats=selected))
        async def album(event):
            await capture(event.messages, event.chat_id, await event.get_chat())

        @client.on(events.MessageEdited(chats=selected))
        async def edited(event):
            await capture(await edited_messages(client, event), event.chat_id, await event.get_chat())

        await recover_recent_messages()
        print(f'Monitorando {len(set(selected))} chats. Ctrl+C para parar.')
        await client.run_until_disconnected()
    finally:
        if captured_queue is not None:
            captured_queue.close()
        await client.disconnect()

if __name__ == '__main__':
    try:
        from execucao_unica import instancia_unica
        with instancia_unica(BASE / 'monitor.lock'):
            asyncio.run(main())
    except KeyboardInterrupt:
        print('Monitor encerrado.')
