"""Captura ofertas novas em grupos escolhidos. Use --listar para ver os IDs."""
import argparse
import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient, events
from ofertas_core import extract_links, resolve, price, coupon, coupon_page_links, safe_url

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / '.env', encoding='utf-8-sig')
logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')

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
            print('Código de cupom explícito:', coupon(raw) or 'ausente')
            print('Foto nesta mensagem:', bool(message.photo), '| pertence a álbum:', bool(message.grouped_id))
            excluded = coupon_page_links(raw)
            for i, url in enumerate(extract_links(message), 1):
                if url in excluded:
                    print('Link', i, ': página de cupons; não é tratado como produto.')
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

        async def capture(messages, chat_id, chat):
            if not chat or getattr(chat, 'noforwards', False) or any(getattr(m, 'noforwards', False) for m in messages):
                print('Ignorada: conteúdo protegido ou chat indisponível.', chat_id)
                return
            text = '\n'.join(m.raw_text or '' for m in messages)
            excluded = coupon_page_links(text)
            urls = list(dict.fromkeys(u for m in messages for u in extract_links(m) if u not in excluded and safe_url(u)))
            products = {}
            for url in urls:
                try:
                    p = await asyncio.to_thread(resolve, url)
                    if p:
                        products[p[0]] = p
                except Exception:
                    logging.warning('Não foi possível identificar um link na mensagem %s.', messages[0].id)
            # Não associa uma única imagem/preço a vários produtos diferentes.
            if len(products) != 1:
                if products:
                    print('Ignorada: mensagem contém vários produtos.')
                elif urls:
                    print('Ignorada: não foi possível resolver o link do produto.', chat_id, messages[0].id)
                return
            key, store, direct_url = next(iter(products.values()))
            if store != 'Shopee':
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
            row = {'product_id': key, 'store': store, 'url': direct_url, 'source': 'telegram',
                   'price': price(text), 'coupon': coupon(text), 'image': image,
                   'chat_id': chat_id, 'message_id': messages[0].id,
                   'captured_at': datetime.now(timezone.utc).isoformat(),
                   'source_date': messages[0].date.isoformat()}
            with (BASE / 'fila_ofertas_v2.jsonl').open('a', encoding='utf-8') as out:
                out.write(json.dumps(row, ensure_ascii=False) + '\n')
            print('Oferta captada:', key, '| imagem:', bool(image), '| cupom:', row['coupon'] or 'não identificado')

        @client.on(events.NewMessage(chats=selected))
        async def receive(event):
            if not event.message.grouped_id:
                await capture([event.message], event.chat_id, await event.get_chat())

        @client.on(events.Album(chats=selected))
        async def album(event):
            await capture(event.messages, event.chat_id, await event.get_chat())

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
