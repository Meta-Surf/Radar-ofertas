"""Radar contínuo por temas. Prévia padrão; --publicar habilita envios reais."""
import argparse
import math
import os
import re
import unicodedata
import time
from pathlib import Path

TEMAS = [
    ('Televisores', 'smart tv'),
    ('Placas de vídeo', 'placa de video'),
    ('Processadores (CPU)', 'processador'),
    ('Memória RAM', 'memoria ram'),
    ('Notebooks', 'notebook'),
    ('Tablets', 'tablet'),
    ('Smartphones', 'smartphone'),
    ('Mouses', 'mouse'),
    ('Teclados', 'teclado'),
    ('SSDs', 'ssd'),
    ('Monitores', 'monitor gamer'),
    ('Placas-mãe', 'placa mae'),
    ('Fontes de alimentação', 'fonte atx'),
    ('Gabinetes', 'gabinete pc'),
    ('Caixas de som', 'caixa de som bluetooth'),
    ('Fones de ouvido', 'fone de ouvido'),
    ('Roteadores', 'roteador wifi'),
    ('Webcams', 'webcam'),
    ('Impressoras', 'impressora'),
    ('Consoles', 'console videogame'),
    ('Smartwatches', 'smartwatch'),
    ('Acessórios de informática', 'hub usb'),
]

# Heurísticas de título: reduzem acessórios, mas não certificam modelo,
# autenticidade, condição ou especificações. Não relaxar os filtros se faltar oferta.
REGRAS_TEMA = {
    'Televisores': (r'\b(tv|televisor|televisao)\b',
                   r'\b(controle|suporte|antena|conversor|receptor|box|placa|tela|display|cabo|fonte|backlight)\b'),
    'Placas de vídeo': (r'\b(placa de video|gpu|geforce|radeon|rtx|gtx)\b',
                       r'\b(suporte|cabo|adaptador|riser|cooler|ventoinha|backplate|dissipador|notebook|computador|gabinete)\b'),
    'Processadores (CPU)': (r'\b(processador|cpu|ryzen|xeon|intel core)\b',
                           r'\b(cooler|ventoinha|dissipador|pasta termica|suporte|placa mae|kit|computador|notebook|alimentos)\b'),
    'Memória RAM': (r'\b(ram|ddr[345])\b',
                   r'\b(dissipador|adaptador|suporte|placa mae|computador|notebook completo|gabinete)\b'),
    'Notebooks': (r'\b(notebook|laptop|macbook|chromebook)\b',
                  r'\b(capa|case|bolsa|mochila|suporte|carregador|fonte|bateria|pelicula|teclado|tela|display|dobradica|carcaca|adesivo|cooler)\b'),
    'Tablets': (r'\b(tablet|ipad)\b',
                r'\b(capa|case|pelicula|suporte|caneta|teclado|carregador|cabo|tela|display|bateria|infantil de desenho|mesa digitalizadora)\b'),
    'Smartphones': (r'\b(smartphone|celular|iphone)\b',
                    r'\b(capa|capinha|case|pelicula|suporte|carregador|cabo|tela|display|bateria|carcaca|lente|camera de reposicao)\b'),
    'Mouses': (r'\bmouse\b',
               r'\b(mousepad|mouse pad|tapete|skates|adesivo|receptor|cabo de reposicao|teclado|capa)\b'),
    'Teclados': (r'\bteclado\b',
                 r'\b(keycaps?|teclas?|switch(?:es)? avulsos?|capa|adesivo|reposicao|notebook|musical|piano)\b'),
    'SSDs': (r'\bssd\b',
             r'\b(case|gaveta|adaptador|cabo|dissipador|suporte|notebook|computador|gabinete)\b'),
    'Monitores': (r'\bmonitor\b',
                  r'\b(suporte|braco|cabo|adaptador|fonte|placa|tela de reposicao|display de reposicao|cardiaco|pressao|bebe)\b'),
    'Placas-mãe': (r'\b(placa mae|motherboard)\b',
                  r'\b(suporte|cabo|adaptador|espelho|backplate|soquete|reparo|conector|kit|computador|notebook)\b'),
    'Fontes de alimentação': (r'\b(fonte atx|fonte de alimentacao|psu)\b',
                             r'\b(cabo|conector|adaptador|carregador|notebook|modulo|placa|televisor|tv|led|fita)\b'),
    'Gabinetes': (r'\b(gabinete|case pc)\b',
                  r'\b(suporte|fan|cooler|ventoinha|grade|filtro|cabo|parafuso|bolsa|capa|mesa|computador completo)\b'),
    'Caixas de som': (r'\b(caixa de som|speaker|soundbar)\b',
                      r'\b(capa|case|suporte|cabo|carregador|bateria|placa|controle|alto falante avulso)\b'),
    'Fones de ouvido': (r'\b(fone de ouvido|headphone|headset|earbuds|earphone)\b',
                       r'\b(capa|case|estojo avulso|almofada|espuma|cabo|suporte|adaptador|reparo)\b'),
    'Roteadores': (r'\b(roteador|router|mesh wifi|wi fi mesh)\b',
                  r'\b(antena avulsa|suporte|cabo|fonte|carregador|adaptador|repetidor|modem de reposicao)\b'),
    'Webcams': (r'\b(webcam|camera para pc|camera usb)\b',
                r'\b(capa|suporte|cabo|adaptador|pelicula|trip[eé])\b'),
    'Impressoras': (r'\b(impressora|multifuncional)\b',
                   r'\b(cartucho|toner|tinta|papel|cilindro|cabeca de impressao|cabo|suporte|pecas|filamento|3d)\b'),
    'Consoles': (r'\b(playstation [345]|ps[345]|xbox|nintendo switch|steam deck|console de videogame)\b',
                 r'\b(controle|joystick|capa|case|suporte|carregador|cabo|jogo|game|midia|pelicula|pecas|reparo)\b'),
    'Smartwatches': (r'\b(smartwatch|smart watch|relogio inteligente|apple watch|galaxy watch)\b',
                     r'\b(pulseira|bracelete|capa|case|pelicula|carregador|cabo|suporte|bateria|reparo)\b'),
    'Acessórios de informática': (r'\b(hub usb|dock station|docking station|adaptador usb|mouse pad|mousepad|suporte para notebook)\b',
                                  r'\b(computador completo|notebook completo|kit com notebook|kit com computador)\b'),
}


def pertence_ao_tema(theme, offer):
    title = ''.join(c for c in unicodedata.normalize('NFKD', str(offer.get('name', '')))
                    if not unicodedata.combining(c)).lower()
    title = re.sub(r'[-_/]+', ' ', title)
    positive, negative = REGRAS_TEMA[theme]
    if re.search(r'\b(para retirada de pecas|sucata|com defeito|nao funciona|caixa vazia)\b', title):
        return False
    return bool(re.search(positive, title)) and not re.search(negative, title)


def score(offer):
    # Prioriza reputação e vendas; limita o peso do desconto informado.
    return (40 * offer['rating'] / 5
            + 35 * min(math.log10(1 + offer['sales']) / 4, 1)
            + 25 * min(offer['discount'], 60) / 60)


def select(groups, limit, preserve_order=False):
    selected, seen = [], set()
    # Em loop, respeita a rotação de temas; na prévia única, usa o ranking.
    if not preserve_order:
        groups = sorted(groups, key=lambda g: max((score(o) for o in g[1]), default=-1), reverse=True)
    for theme, offers in groups:
        for offer in sorted(offers, key=score, reverse=True):
            if offer['product_id'] not in seen:
                selected.append((theme, offer))
                seen.add(offer['product_id'])
                break
        if len(selected) >= limit:
            break
    return selected


def publish_one(client, ledger, offer, token, channel, send):
    key = offer['product_id']
    day = ledger.reserve(key)
    if day is None:
        return 'duplicada', None
    # Antes de enviar, falhas na preparação podem liberar a reserva.
    try:
        prepared = client.prepare(offer)
        if offer.get('tema_radar') and not pertence_ao_tema(offer['tema_radar'], prepared):
            raise ValueError('Título revalidado não corresponde ao tema')
        if not prepared.get('api_image'):
            raise ValueError('Oferta sem imagem')
    except Exception:
        ledger.release(key, day)
        return 'revalidacao_falhou', None
    try:
        message_id, _ = send(token, channel, prepared, prepared['api_image'])
    except Exception:
        # Timeout/JSON inesperado: não repetir, pois o Telegram pode ter recebido.
        return 'incerta', None
    if message_id is None:
        ledger.release(key, day)
        return 'rejeitada', None
    try:
        ledger.finish(key, day, message_id)
    except Exception:
        return 'enviada_registro_pendente', message_id
    return 'publicada', message_id


def run_round(args, parser):
    try:
        from dotenv import load_dotenv
        from radar_shopee import collect
        from shopee_afiliados import ShopeeAffiliate
        from ofertas_core import Ledger, caption
        from bot_ofertas_revisao import send
    except ImportError:
        parser.exit(1, 'Coloque este arquivo junto de radar_shopee.py e instale requirements.txt.\n')
    base = Path(__file__).resolve().parent
    load_dotenv(base / '.env', encoding='utf-8-sig')
    token, channel = os.getenv('TELEGRAM_TOKEN'), os.getenv('TELEGRAM_CANAL')
    if args.publicar and (not token or not channel):
        parser.exit(1, 'Faltam TELEGRAM_TOKEN/TELEGRAM_CANAL no .env local.\n')
    try:
        client = ShopeeAffiliate.from_env()
        interval = max(300, int(os.getenv('INTERVALO_PUBLICACOES', '300')))
    except Exception:
        parser.exit(1, 'Confira SHOPEE_APP_ID, SHOPEE_SECRET e INTERVALO_PUBLICACOES no .env.\n')
    print('PUBLICAÇÃO REAL. Destino:', channel) if args.publicar else print('PRÉVIA: nada será publicado.')
    print('Filtros: desconto informado >=20%, nota >=4,5 e vendas >=50; imagem e período ativo.')
    print(f'Tecnologia: {len(TEMAS)} temas, com filtros de título por categoria. Ranking não comprova menor preço histórico.')
    groups = []
    for theme, query in TEMAS:
        try:
            offers, scanned = collect(client, query, pages=2, minimum_discount=20,
                                      minimum_rating=4.5, minimum_sales=50)
        except Exception:
            parser.exit(1, f'Consulta falhou em {theme}. Nada foi enviado nesta rodada. Rode py radar_shopee.py --buscar "{query}" para diagnóstico.\n')
        aprovados_api = len(offers)
        offers = [dict(o, tema_radar=theme) for o in offers if pertence_ao_tema(theme, o)]
        print(f'{theme}: {scanned} consultados; {aprovados_api} aprovados nos filtros; {len(offers)} após filtro de tema.')
        groups.append((theme, offers))
        time.sleep(1)
    # Retira publicadas e reservas incertas de qualquer data antes do ranking.
    if args.publicar:
        registry = Ledger(base / 'publicacoes.sqlite3')
        try:
            blocked = {row[0] for row in registry.db.execute(
                'SELECT product FROM posts')}
        finally:
            registry.db.close()
        groups = [(theme, [o for o in offers if o['product_id'] not in blocked])
                  for theme, offers in groups]
    # Alterna prioridade entre temas; escolhe o melhor produto de cada tema.
    if args.loop:
        offset = args.round_index % len(TEMAS)
        priority = [theme for theme, _ in TEMAS[offset:] + TEMAS[:offset]]
        available = {theme: offers for theme, offers in groups if offers}
        chosen = [(theme, available[theme]) for theme in priority if theme in available]
        selected = select(chosen, args.limite, preserve_order=True)
    else:
        selected = select(groups, args.limite)
    if not selected:
        print('Nenhuma oferta elegível. Nenhum envio realizado; filtros preservados.')
        return
    print('\nSELEÇÃO:')
    for theme, offer in selected:
        prefix = 'a partir de ' if offer.get('price_from') else ''
        print(f"[{theme}] {offer['name']} | {prefix}R$ {offer['price']} | desconto informado {offer['discount']:g}% | nota {offer['rating']:g} | vendas {offer['sales']}")
    if not args.publicar:
        for theme, offer in selected:
            try:
                prepared = client.prepare(offer)
                if not pertence_ao_tema(theme, prepared):
                    raise ValueError('Título revalidado não corresponde ao tema')
            except Exception:
                print(f'[{theme}] Falha de revalidação/afiliado; não estaria apta a publicar.')
                continue
            print('\n' + caption(prepared))
            print('Link afiliado:', prepared['affiliate_url'])
        print('\nPrévia concluída. --publicar faz nova busca e pode selecionar produtos diferentes.')
        return
    ledger = Ledger(base / 'publicacoes.sqlite3')
    confirmed = 0
    try:
        for index, (theme, offer) in enumerate(selected):
            if index:
                time.sleep(interval)
            status, message_id = publish_one(client, ledger, offer, token, channel, send)
            print(f'[{theme}] {status} | produto {offer["product_id"]} | mensagem {message_id or "-"}')
            if status == 'publicada':
                confirmed += 1
            if status in ('incerta', 'enviada_registro_pendente', 'rejeitada'):
                print('Rodada encerrada. Confira o canal. Nenhuma repetição automática nesta execução.')
                break
    finally:
        ledger.db.close()
    print(f'Fim da rodada: {confirmed} publicações confirmadas e registradas.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publicar', action='store_true', help='Envia ao TELEGRAM_CANAL configurado no .env.')
    parser.add_argument('--loop', action='store_true', help='Mantém a consulta e publicação periódicas até Ctrl+C.')
    parser.add_argument('--intervalo', type=int, default=300, help='Segundos entre rodadas; padrão 300 (5 min).')
    parser.add_argument('--limite', type=int, default=1, choices=range(1, 4), help='Máximo por rodada; padrão 1.')
    args = parser.parse_args()
    if args.intervalo < 300:
        parser.error('--intervalo deve ser pelo menos 300 segundos.')
    args.round_index = 0
    while True:
        started = time.monotonic()
        try:
            run_round(args, parser)
        except SystemExit:
            # Erro de API/configuração: interrompe sem repetir automaticamente envios.
            if not args.loop:
                raise
            print('Rodada sem sucesso; nova consulta no próximo intervalo.')
        except Exception:
            print('Falha operacional; detalhes sensíveis omitidos. Confira rede, arquivos e configuração.')
            if not args.loop:
                raise SystemExit(1)
        if not args.loop:
            break
        args.round_index += 1
        delay = max(args.intervalo - (time.monotonic() - started), 30)
        print(f'Próxima consulta em {delay / 60:.1f} minutos. Ctrl+C encerra.', flush=True)
        time.sleep(delay)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nInterrompido. Se ocorreu durante envio, confira o canal antes de repetir.')
