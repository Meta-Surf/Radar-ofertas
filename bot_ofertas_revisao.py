"""Publicação Shopee com link gerado pela API de Afiliados. --simular não publica."""
import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from ofertas_core import Ledger, caption, product
from shopee_afiliados import ShopeeAffiliate, AffiliateError, valid_affiliate_url

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

def ordered_rows():
    # Prioridade absoluta aos grupos; preserva o ranking da fila do radar.
    groups, radar = [], []
    for offer in rows():
        (radar if offer.get('source') == 'shopee_api' else groups).append(offer)
    groups.sort(key=lambda o: str(o.get('source_date', '')), reverse=True)
    yield from groups
    yield from radar

def photo_path(offer):
    value = offer.get('image')
    if not value:
        return None
    path = (BASE / value).resolve()
    if not path.is_relative_to(BASE / 'imagens_ofertas') or not path.is_file():
        return None
    return path if 0 < path.stat().st_size <= 10_000_000 else None

def send(token, channel, offer, image):
    if not offer.get('affiliate_generated') or not valid_affiliate_url(offer.get('affiliate_url')):
        raise AffiliateError('Publicação bloqueada: falta link gerado pela API de Afiliados.')
    method = 'sendPhoto' if image else 'sendMessage'
    data = {'chat_id': channel, 'parse_mode': 'HTML',
            'reply_markup': json.dumps({'inline_keyboard': [[{'text': '🛒 VER OFERTA', 'url': offer['affiliate_url']}]]})}
    data['caption' if image else 'text'] = caption(offer)
    endpoint = f'https://api.telegram.org/bot{token}/{method}'
    if isinstance(image, str):
        data['photo'] = image
        response = requests.post(endpoint, data=data, timeout=(10, 45))
    elif image:
        with image.open('rb') as f:
            response = requests.post(endpoint, data=data, files={'photo': (image.name, f, 'image/jpeg')}, timeout=(10, 45))
    else:
        data['link_preview_options'] = json.dumps({'is_disabled': True})
        response = requests.post(endpoint, data=data, timeout=(10, 45))
    # Somente ok:false é uma rejeição inequívoca. HTTP/JSON inesperado é incerto.
    result = response.json()
    if result.get('ok') is False:
        return None, int(result.get('parameters', {}).get('retry_after', 60))
    return int(result['result']['message_id']), 0

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
        parser.error(str(e))
    require_photo = os.getenv('EXIGIR_IMAGEM', '1') == '1'
    interval = 600  # Intervalo exclusivo das publicações originadas no radar.
    max_age = max(1, int(os.getenv('IDADE_MAXIMA_MINUTOS', '120')))
    ledger = Ledger(BASE / 'publicacoes.sqlite3')
    announced = set()
    retry_at = {}
    print('Simulação com API Shopee: nada será publicado.' if args.simular else 'Publicador afiliado Shopee ativo. Ctrl+C para parar.')
    while True:
        if not args.simular:
            delay = ledger.publication_delay(clock_id=3)
            if delay > 0:
                time.sleep(min(delay, 1))
                continue
        for offer in ordered_rows():
            key = offer.get('product_id')
            checked = product(offer.get('url', ''))
            if not checked or checked[0] != key or checked[2] != offer['url']:
                continue
            offer['store'] = checked[1]
            if checked[1] != 'Shopee':
                if key not in announced:
                    print('Aguardando integração de afiliados da loja:', checked[1])
                    announced.add(key)
                continue
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(offer['source_date'])).total_seconds()
            except (KeyError, TypeError, ValueError):
                continue
            if age < -60 or age > max_age * 60:
                continue
            if time.monotonic() < retry_at.get(key, 0):
                continue
            if args.simular and key in announced:
                continue
            origin = 'radar' if offer.get('source') == 'shopee_api' else 'telegram'
            if not args.simular and origin == 'radar' and ledger.publication_delay(clock_id=2) > 0:
                continue
            day = None if args.simular else ledger.reserve(key)
            if not args.simular and not day:
                continue
            theme = offer.get('tema_radar')
            try:
                offer = affiliate.prepare(offer)
                if theme:
                    from radar_shopee_continuo import pertence_ao_tema
                    if not pertence_ao_tema(theme, offer):
                        raise AffiliateError('Título atualizado fora do tema; oferta ignorada.')
            except AffiliateError as e:
                if day:
                    ledger.release(key, day)
                print(str(e))
                retry_at[key] = time.monotonic() + 300
                continue
            image = offer.get('api_image') or photo_path(offer)
            if require_photo and not image:
                if key not in announced:
                    print('Aguardando imagem autorizada:', key)
                    announced.add(key)
                if day:
                    ledger.release(key, day)
                retry_at[key] = time.monotonic() + 60
                continue
            if args.simular:
                if key not in announced:
                    print('\n', key, '\n', caption(offer), '\n', offer['affiliate_url'], '\nImagem:', bool(image))
                    announced.add(key)
                continue
            try:
                # Grava antes do envio: falha ou reinício não encurta a pausa.
                if origin == 'radar':
                    ledger.mark_attempt(interval, clock_id=2)
                message_id, wait = send(token, channel, offer, image)
            except Exception:
                print('Envio com resultado incerto. Produto bloqueado no histórico:', key,
                      '(Confira o canal; detalhes sensíveis foram omitidos.)')
                break
            if message_id is None:
                ledger.release(key, day)
                print('Telegram rejeitou a publicação:', key, '| aguardando para tentar novamente.')
                retry_at[key] = time.monotonic() + max(wait, 1)
                ledger.mark_attempt(max(wait, 1), clock_id=3)
                break
            ledger.finish(key, day, message_id)
            print('Publicado:', key, '| origem:', origin, '| mensagem', message_id)
            break  # Lê novamente as filas e verifica a idade após a espera.
        if args.simular:
            break
        time.sleep(1)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Publicador encerrado.')
