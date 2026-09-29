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
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient, events
from ofertas_core import extract_links, resolve, price, price_info, coupon, coupon_page_links, safe_url
from cupons_shopee import build_alerts, coupon_entries
from cupons_mercadolivre import build_alert as build_ml_alert
import mercadolivre_manual as ml_manual
from shopee_afiliados import AffiliateError

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
    api_id, api_hash = os.getenv('TG_API_ID', ''), os.getenv('TG_API_HASH', '')
    if not api_id.isdigit() or not api_hash:
        parser.error('Preencha TG_API_ID e TG_API_HASH no .env.')
    client = TelegramClient(str(BASE / 'monitor_ofertas'), int(api_id), api_hash, sequential_updates=True)
    await client.start()
    try:
        dialogs = [d async for d in client.iter_dialogs() if d.is_group or d.is_channel]
        if args.listar:
            for d in dialogs:
                print(d.id, d.name)
            return
        requested = [x.strip() for x in os.getenv('TG_CHATS', '').split(',') if x.strip()]
        if ml_manual.chat_id() and ml_manual.chat_id() not in requested:
            if any(str(d.id) == ml_manual.chat_id() for d in dialogs):
                requested.append(ml_manual.chat_id())
            else:
                print('Grupo manual ML não encontrado nesta conta; os demais grupos continuam ativos:', ml_manual.chat_id())
        if not requested:
            parser.error('Preencha TG_CHATS com IDs negativos ou @nomes da lista.')
        selected = []
        for value in requested:
            match = next((d for d in dialogs if str(d.id) == value or
                          '@' + str(getattr(d.entity, 'username', '')).lower() == value.lower()), None)
            if not match:
                parser.error('Chat não encontrado: ' + value + '. Confira o sinal de menos no ID.')
            if str(match.id) == os.getenv('TELEGRAM_CANAL', '') or ('@' + str(getattr(match.entity, 'username', '')).lower()) == os.getenv('TELEGRAM_CANAL', '').lower():
                parser.error('Remova o canal de destino de TG_CHATS para evitar ciclos.')
            selected.append(match.id)
        allowed_media = {x.strip() for x in os.getenv('TG_MEDIA_CHATS', '').split(',') if x.strip()}
        if ml_manual.chat_id():
            allowed_media.add(ml_manual.chat_id())
        if args.diagnosticar:
            match = re.fullmatch(r'https://t\.me/([A-Za-z0-9_]+)/(\d+)/?', args.diagnosticar)
            if not match:
                parser.error('Use um link como https://t.me/nomecanal/12345')
            dialog = next((d for d in dialogs if (getattr(d.entity, 'username', '') or '').lower() == match[1].lower()), None)
            if not dialog:
                parser.error('Canal não encontrado entre os chats da sua conta.')
            print('Canal:', dialog.name, '| ID:', dialog.id)
            print('Está em TG_CHATS:', dialog.id in selected)
            print('Imagens autorizadas em TG_MEDIA_CHATS:', str(dialog.id) in allowed_media)
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
                        print('Produto:', p[0] if p else 'não identificado')
                    except Exception as e:
                        print('Falha de rede ou formato:', type(e).__name__)
            print('Diagnóstico encerrado. Nada foi publicado ou colocado na fila.')
            return
        media_dir = BASE / 'imagens_ofertas'
        media_dir.mkdir(exist_ok=True)

        revisions = CaptureRevisions()
        capture_lock = asyncio.Lock()

        async def capture_once(messages, chat_id, chat):
            if not chat or getattr(chat, 'noforwards', False) or any(getattr(m, 'noforwards', False) for m in messages):
                print('Ignorada: conteúdo protegido ou chat indisponível.', chat_id)
                return
            text = '\n'.join(m.raw_text or '' for m in messages)
            ml_alert = ml_manual.build_coupon(messages, chat_id)
            if ml_alert:
                with (BASE / 'fila_ofertas_v2.jsonl').open('a', encoding='utf-8') as out:
                    out.write(json.dumps(ml_alert, ensure_ascii=False) + '\n')
                print('Lista de cupons Mercado Livre captada:', len(ml_alert['entries']), '| uma publicação, sem links de terceiros.')
                return
            if ml_manual.trusted(chat_id):
                try:
                    row = ml_manual.build_offer(messages, chat_id)
                except AffiliateError as error:
                    print(str(error), '| mensagem', messages[0].id)
                    return
                photo = next((m for m in messages if m.photo), None)
                if photo:
                    try:
                        target = media_dir / f'{chat_id}_{photo.id}.jpg'
                        downloaded = await client.download_media(photo, file=str(target))
                        if downloaded and target.exists() and 0 < target.stat().st_size <= 10_000_000:
                            row['image'] = str(target.relative_to(BASE))
                    except Exception:
                        logging.warning('Imagem manual indisponível na mensagem %s.', photo.id)
                with (BASE / 'fila_ofertas_v2.jsonl').open('a', encoding='utf-8') as out:
                    out.write(json.dumps(row, ensure_ascii=False) + '\n')
                print('Oferta manual Mercado Livre captada:', row['product_id'], '| preço:', row['price'] or 'aguardando leitura automática do link')
                return
            alerts = build_alerts(messages, chat_id)
            if alerts:
                with (BASE / 'fila_ofertas_v2.jsonl').open('a', encoding='utf-8') as out:
                    for alert in alerts:
                        out.write(json.dumps(alert, ensure_ascii=False) + '\n')
                print('Alerta de cupons captado:', sum(len(a['entries']) for a in alerts), '| links aguardando conversão.')
            excluded = coupon_page_links(text) | {e['url'] for a in alerts for e in a['entries']}
            urls = list(dict.fromkeys(u for m in messages for u in extract_links(m) if u not in excluded and safe_url(u)))
            products = {}
            unresolved = []
            for url in urls:
                p, reason = await resolve_for_capture(url)
                if p:
                    products[p[0]] = p
                else:
                    unresolved.append(reason)
            # Um segundo link não resolvido pode esconder outro produto.
            if unresolved:
                print('Ignorada: não foi possível resolver todos os links do produto.',
                      chat_id, messages[0].id, '|', ' | '.join(dict.fromkeys(unresolved)))
                return
            # Não associa uma única imagem/preço a vários produtos diferentes.
            if len(products) != 1:
                if products:
                    print('Ignorada: mensagem contém vários produtos.')
                elif urls:
                    print('Ignorada: não foi possível resolver o link do produto.', chat_id, messages[0].id)
                return
            key, store, direct_url = next(iter(products.values()))
            if store not in ('Shopee', 'Mercado Livre'):
                return
            image = None
            if str(chat_id) in allowed_media:
                photo = next((m for m in messages if m.photo), None)
                if photo:
                    try:
                        target = media_dir / f'{chat_id}_{photo.id}.jpg'
                        downloaded = await client.download_media(photo, file=str(target))
                        if downloaded and target.exists() and 0 < target.stat().st_size <= 10_000_000:
                            image = str(target.relative_to(BASE))
                        elif target.exists():
                            target.unlink()
                    except Exception:
                        logging.warning('Imagem indisponível na mensagem %s.', photo.id)
            captured_price = price_info(text) or {}
            title_lines = [re.sub(r'[*_`]', '', line).strip() for line in text.splitlines()]
            title = next((line for line in title_lines
                          if line and not re.search(r'https?://|R\\$|[💵💰💸]|\\b(?:cupom|link|compre|resgate)\\b', line, re.I)), '')
            row = {'product_id': key, 'store': store, 'url': direct_url, 'source': 'telegram',
                   'kind': 'ml_offer' if store == 'Mercado Livre' else 'product_offer',
                   'name': title[:160],
                   'price': captured_price.get('price'),
                   'price_condition': captured_price.get('price_condition', ''),
                   'price_from': captured_price.get('price_from', False),
                   'coupon': coupon(text), 'image': image,
                   'chat_id': chat_id, 'message_id': min(m.id for m in messages),
                   'captured_at': datetime.now(timezone.utc).isoformat(),
                   'source_date': messages[0].date.isoformat()}
            with (BASE / 'fila_ofertas_v2.jsonl').open('a', encoding='utf-8') as out:
                out.write(json.dumps(row, ensure_ascii=False) + '\n')
            print('Oferta captada:', key, '| preço:', row['price'] or 'não identificado/ambíguo',
                  '| condição:', row['price_condition'] or 'nenhuma',
                  '| imagem:', bool(image), '| cupom:', row['coupon'] or 'não identificado')

        async def capture(messages, chat_id, chat):
            async with capture_lock:
                key, digest = revisions.identity(messages, chat_id)
                if revisions.unchanged(key, digest):
                    return
                await capture_once(messages, chat_id, chat)
                revisions.remember(key, digest)

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

        print(f'Monitorando {len(set(selected))} chats. Ctrl+C para parar.')
        await client.run_until_disconnected()
    finally:
        await client.disconnect()

if __name__ == '__main__':
    try:
        from execucao_unica import instancia_unica
        with instancia_unica(BASE / 'monitor.lock'):
            asyncio.run(main())
    except KeyboardInterrupt:
        print('Monitor encerrado.')
