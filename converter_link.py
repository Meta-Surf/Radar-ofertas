"""Converte para afiliado Shopee sem publicar; aceita destino informado pelo usuário."""
import argparse
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlparse
from dotenv import load_dotenv
from ofertas_core import PRODUCT_REDIRECTORS, product, resolve, safe_url
from shopee_afiliados import AffiliateError, ShopeeAffiliate

BASE = Path(__file__).resolve().parent


def register_destination(source, destination, path):
    if not safe_url(source) or urlparse(source).hostname not in PRODUCT_REDIRECTORS:
        raise ValueError('A origem deve ser um link HTTPS do redirecionador reconhecido.')
    identified = product(destination)
    if not identified or identified[1] != 'Shopee':
        raise ValueError('Copie do navegador o endereço direto do produto na Shopee, com os IDs da loja e do item.')
    path = Path(path)
    mappings = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if not isinstance(mappings, dict):
        raise ValueError('O arquivo de destinos precisa conter um objeto JSON.')
    mappings[source.split('#', 1)[0].rstrip('/')] = identified[2]
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as f:
            temporary = Path(f.name)
            json.dump(mappings, f, ensure_ascii=False, indent=2)
            f.write('\n')
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return identified


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url', help='Link do produto ou do redirecionador.')
    parser.add_argument('--destino', help='URL direta do mesmo produto Shopee, copiada após abrir a origem no navegador.')
    parser.add_argument('--somente-diagnosticar', action='store_true', help='Não acessa a API e não gera link.')
    args = parser.parse_args(argv)
    load_dotenv(BASE / '.env', encoding='utf-8-sig')
    try:
        if args.destino:
            identified = register_destination(args.url, args.destino, BASE / 'destinos_confirmados.json')
            print('Destino informado salvo para este link exato; outras ofertas não usam este cadastro.')
        else:
            identified = resolve(args.url, print)
        if not identified or identified[1] != 'Shopee':
            print('Não foi identificado um produto Shopee. Nenhum link de afiliado foi gerado.')
            print('Se o redirecionador exigir navegador, abra a origem normalmente e copie a URL final do produto.')
            print('Depois execute novamente com --destino "URL_DIRETA_DO_MESMO_PRODUTO_SHOPEE".')
            return 2
        print('Produto:', identified[0])
        print('Destino normalizado:', identified[2])
        if not args.somente_diagnosticar:
            link = ShopeeAffiliate.from_env().generate_link(identified[2])
            print('Seu link de afiliado:', link)
        print('Nenhuma publicação foi enviada ao Telegram.')
        return 0
    except (AffiliateError, ValueError, OSError) as error:
        print('Conversão não concluída:', error)
        return 1
    except Exception as error:
        print('Conversão não concluída:', type(error).__name__)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
