"""Integração com a API de Afiliados Shopee Brasil. Nunca imprime segredos."""
import hashlib
import json
import os
import time
from pathlib import Path
from urllib.parse import urlparse
from ofertas_core import product, resolve

ENDPOINT = 'https://open-api.affiliate.shopee.com.br/graphql'

class AffiliateError(Exception):
    pass

def valid_affiliate_url(url):
    if not isinstance(url, str):
        return False
    try:
        p = urlparse(url)
        return p.scheme == 'https' and not p.username and p.port in (None, 443) and p.hostname in (
            's.shopee.com.br', 'shope.ee', 'shopee.com.br', 'www.shopee.com.br')
    except ValueError:
        return False

def valid_image_url(url):
    if not isinstance(url, str):
        return False
    p = urlparse(url)
    host = p.hostname or ''
    return p.scheme == 'https' and not p.username and any(host.endswith('.' + d) or host == d
        for d in ('susercontent.com', 'shopee.com.br', 'shopee.com'))

class ShopeeAffiliate:
    def __init__(self, app_id, secret, sub_id='telegram', transport=None):
        self.app_id = app_id.strip()
        self.secret = secret.strip()
        self.sub_id = sub_id.strip()
        if not self.app_id.isdigit() or not self.secret:
            raise AffiliateError('Preencha SHOPEE_APP_ID e SHOPEE_SECRET no .env com as credenciais da API de Afiliados.')
        if not self.sub_id.isalnum() or len(self.sub_id) > 30:
            raise AffiliateError('SHOPEE_SUB_ID deve conter de 1 a 30 letras/números, sem espaços.')
        if transport is None:
            import requests
            transport = requests
        self.transport = transport

    @classmethod
    def from_env(cls):
        return cls(os.getenv('SHOPEE_APP_ID', ''), os.getenv('SHOPEE_SECRET', ''), os.getenv('SHOPEE_SUB_ID', 'telegram'))

    def request(self, query):
        body = json.dumps({'query': query}, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        timestamp = str(int(time.time()))
        signature = hashlib.sha256(self.app_id.encode() + timestamp.encode() + body + self.secret.encode()).hexdigest()
        headers = {'Content-Type': 'application/json',
                   'Authorization': f'SHA256 Credential={self.app_id}, Timestamp={timestamp}, Signature={signature}'}
        try:
            response = self.transport.post(ENDPOINT, data=body, headers=headers, timeout=(10, 30), allow_redirects=False)
        except Exception:
            raise AffiliateError('Falha de conexão com a API Shopee. A oferta não será publicada.') from None
        if response.status_code != 200:
            code = response.status_code
            raise AffiliateError(f'API Shopee respondeu HTTP {code}. Confira liberação das credenciais, conexão e limites da API.')
        try:
            result = response.json()
        except Exception:
            raise AffiliateError('API Shopee retornou uma resposta que não é JSON.') from None
        if not isinstance(result, dict):
            raise AffiliateError('Formato de resposta inesperado da API Shopee.')
        if result.get('errors'):
            # Nunca imprime a resposta crua: pode incluir dados da requisição.
            message = str(result['errors']).lower()
            if 'signature' in message or 'credential' in message or 'timestamp' in message or 'authentication' in message:
                raise AffiliateError('Autenticação Shopee recusada. Confira App ID, Secret da API e data/hora automática do Windows.')
            raise AffiliateError('A Shopee recusou a operação GraphQL. Confira acesso à API e teste o produto no Explorer oficial.')
        if not isinstance(result.get('data'), dict):
            raise AffiliateError('API Shopee não retornou dados da operação.')
        return result['data']

    def generate_link(self, canonical_url):
        identified = product(canonical_url)
        if not identified or identified[1] != 'Shopee':
            raise AffiliateError('É necessário um link de produto Shopee reconhecido.')
        # A origem é normalizada, sem parâmetros do afiliado do grupo de origem.
        origin = json.dumps(identified[2])
        sub = json.dumps(self.sub_id)
        query = f'mutation {{ generateShortLink(input: {{originUrl: {origin}, subIds: [{sub}]}}) {{ shortLink }} }}'
        data = self.request(query)
        result = data.get('generateShortLink') or {}
        link = result.get('shortLink') if isinstance(result, dict) else None
        if not valid_affiliate_url(link) or link == identified[2]:
            raise AffiliateError('A Shopee não devolveu um link de afiliado válido; publicação bloqueada.')
        return link

    def generate_coupon_link(self, destination):
        from cupons_shopee import canonical_destination
        clean = canonical_destination(destination)
        if not clean or product(clean):
            raise AffiliateError('É necessário um destino Shopee de cupons reconhecido.')
        origin, sub = json.dumps(clean), json.dumps(self.sub_id)
        data = self.request(f'mutation {{ generateShortLink(input: {{originUrl: {origin}, subIds: [{sub}]}}) {{ shortLink }} }}')
        result = data.get('generateShortLink') or {}
        link = result.get('shortLink') if isinstance(result, dict) else None
        if not valid_affiliate_url(link) or link == clean:
            raise AffiliateError('Cupom bloqueado: a API não devolveu um novo link de afiliado válido.')
        return link

    def details(self, key):
        _, shop, item = key.split(':')
        if not shop.isdigit() or not item.isdigit():
            return {}
        query = f'{{ productOfferV2(shopId: {shop}, itemId: {item}, limit: 1) {{ nodes {{ shopId itemId productName imageUrl }} }} }}'
        data = self.request(query)
        connection = data.get('productOfferV2') or {}
        if not isinstance(connection, dict):
            return {}
        nodes = connection.get('nodes') or []
        if not isinstance(nodes, list):
            return {}
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if str(node.get('shopId')) == shop and str(node.get('itemId')) == item:
                return node
        return {}

    def prepare(self, offer):
        if offer.get('source') == 'shopee_api':
            from radar_shopee import refresh
            offer = refresh(self, offer)
        output = dict(offer)
        output['affiliate_url'] = self.generate_link(offer['url'])
        output['affiliate_generated'] = True
        # Falha de metadados não substitui nem invalida o link já gerado.
        try:
            info = self.details(offer['product_id'])
        except AffiliateError:
            info = {}
        if info.get('productName'):
            output['name'] = str(info['productName'])[:160]
        if valid_image_url(info.get('imageUrl')):
            output['api_image'] = info['imageUrl']
        return output

def main():
    import argparse
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / '.env', encoding='utf-8-sig')
    parser = argparse.ArgumentParser(description='Gera um link de afiliado Shopee sem publicar no Telegram.')
    parser.add_argument('--testar', required=True, metavar='URL_PRODUTO')
    args = parser.parse_args()
    try:
        client = ShopeeAffiliate.from_env()
        p = product(args.testar) or resolve(args.testar)
        if not p or p[1] != 'Shopee':
            raise AffiliateError('Produto não identificado. Use o endereço direto da página do produto Shopee.')
        link = client.generate_link(p[2])
    except AffiliateError as e:
        parser.exit(1, str(e) + '\n')
    except Exception:
        parser.exit(1, 'Não foi possível consultar o link. Use o endereço direto do produto.\n')
    print('Link gerado pela API com as credenciais configuradas:')
    print(link)
    print('Nada foi publicado no Telegram. As comissões dependem das regras e da validação da Shopee.')

if __name__ == '__main__':
    main()
