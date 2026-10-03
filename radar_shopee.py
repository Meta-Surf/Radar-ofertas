"""Busca direta na API Shopee. Padrão: prévia sem fila ou publicação."""
import argparse
import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from shopee_afiliados import ShopeeAffiliate, AffiliateError, valid_image_url

BASE = Path(__file__).resolve().parent
FIELDS = ('shopId itemId productName imageUrl priceMin priceMax '
          'priceDiscountRate ratingStar sales periodStartTime periodEndTime')


def number(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('Número inválido')
    return result


def money(value):
    return f'{value:,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')


class ShopeePeriodError(AffiliateError):
    def __init__(self, reason, *, discard=False):
        super().__init__('Oferta Shopee sem período comercial confirmado: ' + reason)
        self.reason, self.discard = reason, discard


def epoch_seconds(value):
    if type(value) is not int and not (isinstance(value, str) and value.isascii() and value.isdigit()):
        raise ValueError('Epoch nativo deve ser inteiro')
    value = int(value)
    if value <= 0:
        raise ValueError('Epoch nativo deve ser positivo')
    return value


def product_offer_period(period, product_id):
    """Prova comercial pura e determinística; nenhum relógio ou API."""
    if not isinstance(period, dict):
        raise ValueError('Período nativo ausente')
    shop, item = str(period.get('shop_id') or ''), str(period.get('item_id') or '')
    if (not shop.isascii() or not item.isascii() or not shop.isdigit() or not item.isdigit()
            or int(shop) <= 0 or int(item) <= 0 or product_id != f'Shopee:{shop}:{item}'):
        raise ValueError('Período de outro produto')
    start, end = epoch_seconds(period.get('start')), epoch_seconds(period.get('end'))
    if end <= start:
        raise ValueError('Período nativo invertido')
    return dict(shop_id=shop, item_id=item, start=start, end=end)


def product_period_status(period, now):
    return 'not_started' if now < period['start'] else ('expired' if now >= period['end'] else 'active')


def active(node, now):
    # Datas ausentes/zero não comprovam período ativo: omite por precaução.
    try:
        start, end = epoch_seconds(node['periodStartTime']), epoch_seconds(node['periodEndTime'])
        return 0 < start <= now < end and end > start
    except (KeyError, ValueError, TypeError):
        return False


def candidate(node, minimum_discount=20, minimum_rating=4.5, minimum_sales=50, minimum_price=0, now=None):
    now = time.time() if now is None else now
    try:
        shop, item = str(node['shopId']), str(node['itemId'])
        if not shop.isdigit() or not item.isdigit() or int(shop) <= 0 or int(item) <= 0:
            return None
        period = product_offer_period(dict(shop_id=shop, item_id=item,
            start=node.get('periodStartTime'), end=node.get('periodEndTime')), f'Shopee:{shop}:{item}')
        low, high = number(node['priceMin']), number(node['priceMax'])
        discount, rating, sales = number(node['priceDiscountRate']), number(node['ratingStar']), number(node['sales'])
        if (not active(node, now) or not 0 < low <= high or low < number(minimum_price) or
                not number(minimum_discount) <= discount <= 100 or
                not number(minimum_rating) <= rating <= 5 or sales < number(minimum_sales) or
                not valid_image_url(node.get('imageUrl')) or not node.get('productName')):
            return None
        stamp = datetime.fromtimestamp(now, timezone.utc).isoformat()
        return {'product_id': f'Shopee:{shop}:{item}', 'store': 'Shopee',
                'url': f'https://shopee.com.br/product/{shop}/{item}',
                'name': str(node['productName'])[:160], 'price': money(low),
                'price_from': high > low, 'discount': float(discount),
                'rating': float(rating), 'sales': int(sales),
                'api_image': node['imageUrl'], 'image': None, 'coupon': None,
                'source': 'shopee_api', 'captured_at': stamp, 'source_date': stamp,
                'shopee_offer_period': period,
                'filter_discount': minimum_discount, 'filter_rating': minimum_rating,
                'filter_sales': minimum_sales, 'filter_price_min': minimum_price}
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return None


def connection(client, operation, args, fields):
    data = client.request(f'{{ {operation}({args}) {{ nodes {{ {fields} }} pageInfo {{ hasNextPage }} }} }}')
    result = data.get(operation)
    if not isinstance(result, dict) or not isinstance(result.get('nodes'), list):
        raise AffiliateError('A Shopee retornou uma lista inesperada. Nenhuma oferta foi enfileirada.')
    return result


def collect(client, keyword='', pages=2, minimum_discount=20, minimum_rating=4.5, minimum_sales=50, minimum_price=0):
    found, scanned = {}, 0
    for page in range(1, pages + 1):
        args = f'page: {page}, limit: 20, sortType: 2'
        if keyword:
            args += ', keyword: ' + json.dumps(keyword)
        result = connection(client, 'productOfferV2', args, FIELDS)
        for node in result['nodes']:
            scanned += 1
            if not isinstance(node, dict):
                continue
            offer = candidate(node, minimum_discount, minimum_rating, minimum_sales, minimum_price)
            if offer:
                found[offer['product_id']] = offer
        if not (result.get('pageInfo') or {}).get('hasNextPage'):
            break
    return sorted(found.values(), key=lambda x: (-x['discount'], -x['rating'], -x['sales'])), scanned


def refresh(client, offer):
    """Reconsulta preços e filtros imediatamente antes de gerar o link/publicar."""
    _, shop, item = offer['product_id'].split(':')
    if not shop.isdigit() or not item.isdigit():
        raise AffiliateError('Identificação do produto inválida.')
    result = connection(client, 'productOfferV2', f'shopId: {shop}, itemId: {item}, limit: 1', FIELDS)
    matching = {json.dumps(node, sort_keys=True):node for node in result['nodes']
                if isinstance(node, dict) and str(node.get('shopId')) == shop and str(node.get('itemId')) == item}
    if len(matching) != 1:
        raise ShopeePeriodError('OFERTA_VALIDADE_NAO_CONFIRMADA')
    node = next(iter(matching.values()))
    try:
        period = product_offer_period(dict(shop_id=shop, item_id=item,
            start=node.get('periodStartTime'), end=node.get('periodEndTime')), offer['product_id'])
    except (TypeError, ValueError):
        raise ShopeePeriodError('OFERTA_VALIDADE_NAO_CONFIRMADA') from None
    state = product_period_status(period, time.time())
    if state != 'active':
        raise ShopeePeriodError('OFERTA_EXPIRADA' if state == 'expired' else 'OFERTA_NAO_INICIADA',
                                discard=state == 'expired')
    updated = candidate(node, offer.get('filter_discount', 20),
                        offer.get('filter_rating', 4.5), offer.get('filter_sales', 50),
                        offer.get('filter_price_min', 0))
    if updated:
        for key, value in offer.items():
            if key == 'tema_radar' or key.startswith('radar_'):
                updated[key] = value
        return updated
    raise AffiliateError('Oferta Shopee indisponível ou fora dos filtros na revalidação; envio bloqueado.')


def main():
    from dotenv import load_dotenv
    load_dotenv(BASE / '.env', encoding='utf-8-sig')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--buscar', default='', help='Palavra ou frase, como fone bluetooth.')
    parser.add_argument('--desconto-min', type=float, default=20)
    parser.add_argument('--nota-min', type=float, default=4.5)
    parser.add_argument('--vendas-min', type=int, default=50)
    parser.add_argument('--preco-min', type=float, default=0, help='Preço mínimo do produto em reais.')
    parser.add_argument('--paginas', type=int, default=2)
    parser.add_argument('--enfileirar', action='store_true', help='Disponibiliza ofertas ao publicador ativo.')
    parser.add_argument('--loop', action='store_true', help='Repete a consulta até Ctrl+C.')
    parser.add_argument('--intervalo', type=int, default=900, help='Segundos entre consultas; mínimo 300.')
    parser.add_argument('--campanhas', action='store_true', help='Mostra campanhas ativas, sem publicar ou validar cupons.')
    args = parser.parse_args()
    if not (0 <= args.desconto_min <= 100 and 0 <= args.nota_min <= 5 and args.vendas_min >= 0
            and args.preco_min >= 0 and 1 <= args.paginas <= 10 and args.intervalo >= 300):
        parser.error('Use desconto 0–100, nota 0–5, vendas/preço >= 0, páginas 1–10 e intervalo >= 300.')
    if args.campanhas and (args.enfileirar or args.loop):
        parser.error('--campanhas é uma consulta única, sem enfileiramento.')
    try:
        client = ShopeeAffiliate.from_env()
        if args.campanhas:
            result = connection(client, 'shopeeOfferV2', 'page: 1, limit: 20',
                                'offerName periodStartTime periodEndTime')
            count = 0
            for node in result['nodes']:
                if isinstance(node, dict) and active(node, time.time()):
                    print(str(node.get('offerName', 'Campanha'))[:200])
                    count += 1
            print(f'{count} campanhas ativas nesta página. Códigos e elegibilidade de cupons não são validados por esta consulta.')
            return
        print('Modo fila: o publicador ativo poderá publicar.' if args.enfileirar else 'Prévia: nenhuma fila será alterada e nada será publicado.')
        while True:
            try:
                offers, scanned = collect(client, args.buscar, args.paginas, args.desconto_min, args.nota_min, args.vendas_min, args.preco_min)
                print(f'Consultados: {scanned} | Aprovados nos filtros: {len(offers)}')
                for offer in offers:
                    prefix = 'a partir de ' if offer['price_from'] else ''
                    print(f"{offer['name']} | {prefix}R$ {offer['price']} | desconto informado {offer['discount']:g}% | nota {offer['rating']:g} | vendas {offer['sales']}")
                if args.enfileirar:
                    from ofertas_core import Ledger
                    from inteligencia_ofertas import Intelligence
                    ledger = Ledger(BASE / 'publicacoes.sqlite3')
                    try:
                        Intelligence(ledger.db).enqueue(offers)
                    finally:
                        ledger.db.close()
                    print('Fila Shopee atualizada. O publicador gera seu link e controla repetição diária.')
                elif not offers:
                    print('Tente outra busca ou ajuste os filtros; esta consulta não cobre todo o catálogo.')
            except AffiliateError:
                # A fila ativa do radar é SQLite; falha de coleta não apaga
                # candidatos válidos ainda dentro do TTL.
                raise
            if not args.loop:
                break
            time.sleep(args.intervalo)
    except AffiliateError as error:
        parser.exit(1, str(error) + '\n')
    except OSError:
        parser.exit(1, 'Não foi possível atualizar a fila local. Confira permissões e se outro radar está rodando.\n')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Radar Shopee encerrado.')

