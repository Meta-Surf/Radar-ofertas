"""Radar contínuo por categorias. Prévia padrão; --publicar habilita envios reais."""
import argparse
import json
import math
import os
import re
import time
import unicodedata
from pathlib import Path

CATALOG_FILE = 'radar_categorias.json'

# Heurísticas de título: reduzem acessórios, peças e falsos positivos.
# Não certificam autenticidade, condição, loja oficial ou especificações.
REGRAS_TEMA = {
    'Refrigeração': (r'\b(cooler|water cooler|ventoinha|fan)\b', r'\b(cabo|suporte|parafuso|adaptador|notebook|placa de video)\b'),
    'Carregadores': (r'\b(carregador|charger)\b', r'\b(cabo avulso|capa|suporte|bateria|placa|conector|kit)\b'),
    'Televisores': (r'\b(tv|televisor|televisao)\b',
                   r'\b(controle|suporte|antena|conversor|receptor|box|placa|tela|display|cabo|fonte|backlight)\b'),
    'Placas de vídeo': (r'\b(placa de video|gpu|geforce|radeon|rtx|gtx)\b',
                       r'\b(suporte|cabo|adaptador|riser|cooler|ventoinha|backplate|dissipador|notebook|computador|gabinete)\b'),
    'Processadores (CPU)': (r'\b(processador|cpu|ryzen|xeon|intel core|core i[3579]|core ultra)\b',
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
    'Fones de ouvido': (r'\b(fone de ouvido|headphone|headset|earbuds|earphone|airpods)\b',
                       r'\b(capa|case|estojo avulso|almofada|espuma|cabo|suporte|adaptador|reparo)\b'),
    'Roteadores': (r'\b(roteador|router|mesh wifi|wi fi mesh)\b',
                  r'\b(antena avulsa|suporte|cabo|fonte|carregador|adaptador|repetidor|modem de reposicao)\b'),
    'Webcams': (r'\b(webcam|camera para pc|camera usb)\b',
                r'\b(capa|suporte|cabo|adaptador|pelicula|tripe)\b'),
    'Impressoras': (r'\b(impressora|multifuncional)\b',
                   r'\b(cartucho|toner|tinta|papel|cilindro|cabeca de impressao|cabo|suporte|pecas|filamento|3d)\b'),
    'Consoles': (r'\b(playstation 5|ps5|xbox series [xs]|nintendo switch|steam deck|rog ally)\b',
                 r'\b(controle|controller|joystick|capa|case|skin|adesivo|suporte|carregador|cabo|jogo|game|midia|pelicula|peca|reparo|compativel com)\b'),
    'Smartwatches': (r'\b(smartwatch|smart watch|relogio inteligente|apple watch|galaxy watch)\b',
                     r'\b(pulseira|bracelete|capa|case|pelicula|carregador|cabo|suporte|bateria|reparo)\b'),
    'Acessórios de informática': (r'\b(hub usb|dock station|docking station|adaptador usb|mouse pad|mousepad|suporte para notebook)\b',
                                  r'\b(computador completo|notebook completo|kit com notebook|kit com computador)\b'),

    'Controles de videogame': (r'\b(dualsense|dual sense|xbox wireless controller|controle xbox|joy con|joycon|pro controller)\b',
                              r'\b(capa|case|skin|adesivo|suporte|cabo|pelicula|peca|analogico|borracha|reparo|compativel|carregador|dock)\b'),
    'Cozinha rápida': (r'\b(air fryer|fritadeira eletrica|forno eletrico|micro ?ondas|cooktop)\b',
                      r'\b(forma|cesto|grade|papel|tapete|peca|resistencia|cabo|puxador|acessorio)\b'),
    'Cafeteiras': (r'\b(cafeteira|maquina de cafe|espresso|expresso)\b',
                  r'\b(capsula|filtro avulso|jarra avulsa|porta filtro|peca|acessorio)\b'),
    'Eletroportáteis de cozinha': (r'\b(liquidificador|mixer|processador de alimentos|batedeira|sanduicheira|grill|chaleira eletrica)\b',
                                  r'\b(copo avulso|jarra avulsa|lamina|peca|acessorio|tampa avulsa)\b'),
    'Limpeza doméstica': (r'\b(aspirador|robo aspirador|mop eletrico|limpador a vapor)\b',
                         r'\b(filtro avulso|saco para aspirador|escova avulsa|bico avulso|peca|acessorio)\b'),
    'Lavanderia': (r'\b(maquina de lavar|lavadora de roupas|lava e seca|secadora de roupas)\b',
                  r'\b(peca|placa|motor|mangueira|filtro|capa|suporte|rolamento|correia)\b'),
    'Climatização': (r'\b(ar condicionado|split inverter|ventilador|climatizador|umidificador)\b',
                    r'\b(controle|placa|motor|helice|capa|suporte|filtro|peca|capacitor|tubulacao)\b'),
    'Linha branca': (r'\b(geladeira|refrigerador|freezer|frigobar|cervejeira)\b',
                    r'\b(peca|placa|motor|compressor avulso|prateleira|gaveta|filtro|borracha|puxador)\b'),
    'Lava-louças e fogões': (r'\b(lava loucas|fogao|forno de embutir)\b',
                            r'\b(peca|placa|motor|mangueira|filtro|grade avulsa|queimador avulso|acessorio)\b'),
    'Ferramentas de perfuração': (r'\b(parafusadeira|furadeira|chave de impacto|martelete)\b',
                                 r'\b(broca avulsa|bit avulso|mandril avulso|bateria avulsa|carregador avulso|peca|adaptador)\b'),
    'Ferramentas de corte': (r'\b(serra circular|serra marmore|tico tico|esmerilhadeira|lixadeira)\b',
                            r'\b(disco avulso|lamina avulsa|lixa avulsa|peca|adaptador|guia avulsa)\b'),
    'Oficina e casa': (r'\b(lavadora de alta pressao|compressor de ar|maquina de solda)\b',
                      r'\b(mangueira avulsa|bico avulso|pistola avulsa|peca|adaptador|consumivel)\b'),
    'Ferramentas a bateria': (r'\b(12v|18v|20v|21v|parafusadeira|furadeira|chave de impacto|serra)\b',
                             r'\b(bateria avulsa|carregador avulso|adaptador|peca|capa|maleta vazia)\b'),
    'Utilidades domésticas': (r'\b(ferro de passar|passadeira a vapor|balanca digital|maquina de costura)\b',
                             r'\b(capa|peca|acessorio|refil|filtro|agulha avulsa|base avulsa)\b'),
    'Casa inteligente': (r'\b(fechadura digital|camera wi ?fi|video porteiro|campainha inteligente|lampada inteligente|tomada inteligente)\b',
                        r'\b(cartao de memoria|cabo|fonte avulsa|suporte|capa|pelicula|peca|adaptador)\b'),
    'Bebê eletrônico': (r'\b(baba eletronica|monitor de bebe|esterilizador|aquecedor de mamadeira)\b',
                       r'\b(capa|suporte|cabo|fonte avulsa|peca|acessorio)\b'),
    'Pets automação': (r'\b(fonte automatica|bebedouro automatico|alimentador automatico|comedouro automatico|maquina de tosa|aspirador pet)\b',
                      r'\b(filtro avulso|refil|lamina avulsa|peca|acessorio|tapete)\b'),
    'Esporte e fitness': (r'\b(bicicleta ergometrica|esteira eletrica|estacao de musculacao|massageador)\b',
                         r'\b(peca|cabo|correia|pedal avulso|suporte|capa|acessorio)\b'),
    'Automotivo portátil': (r'\b(compressor portatil|calibrador eletrico|carregador de bateria|jump starter|partida auxiliar|aspirador automotivo)\b',
                           r'\b(cabo avulso|mangueira avulsa|adaptador|peca|capa|suporte)\b'),
}


def normalized(value):
    return re.sub(r'\s+', ' ', ''.join(c for c in unicodedata.normalize('NFKD', str(value))
                  if not unicodedata.combining(c)).lower()).strip()


def load_catalog(path=None):
    path = Path(path) if path else Path(__file__).resolve().parent / CATALOG_FILE
    data = json.loads(path.read_text(encoding='utf-8'))
    groups, themes = data.get('groups'), data.get('themes')
    if not isinstance(groups, list) or not groups or len(groups) != len(set(groups)):
        raise ValueError('radar_categorias.json: grupos inválidos')
    if not isinstance(themes, list) or not themes:
        raise ValueError('radar_categorias.json: temas ausentes')
    names = set()
    required = ('theme', 'group', 'queries', 'min_price', 'min_discount', 'min_rating', 'min_sales')
    for spec in themes:
        if not isinstance(spec, dict) or any(key not in spec for key in required):
            raise ValueError('radar_categorias.json: tema incompleto')
        theme = str(spec['theme']).strip()
        queries = spec['queries']
        if not theme or theme in names or spec['group'] not in groups:
            raise ValueError('radar_categorias.json: tema duplicado ou grupo inválido')
        if theme not in REGRAS_TEMA:
            raise ValueError(f'radar_categorias.json: faltam regras de título para {theme}')
        if not isinstance(queries, list) or not queries or any(not str(q).strip() for q in queries):
            raise ValueError(f'radar_categorias.json: consultas inválidas em {theme}')
        if not (float(spec['min_price']) >= 0 and 0 <= float(spec['min_discount']) <= 100
                and 0 <= float(spec['min_rating']) <= 5 and int(spec['min_sales']) >= 0):
            raise ValueError(f'radar_categorias.json: filtros inválidos em {theme}')
        premium = spec.get('premium_terms', [])
        if not isinstance(premium, list):
            raise ValueError(f'radar_categorias.json: premium_terms inválido em {theme}')
        names.add(theme)
    return groups, themes


def pertence_ao_tema(theme, offer):
    title = normalized(offer.get('name', ''))
    title = re.sub(r'[-_/]+', ' ', title)
    rule = REGRAS_TEMA.get(theme)
    if not rule:
        return False
    positive, negative = rule
    if re.search(r'\b(para retirada de pecas|sucata|com defeito|nao funciona|caixa vazia)\b', title):
        return False
    return bool(re.search(positive, title)) and not re.search(negative, title)


def premium_match(spec, offer):
    title = normalized(offer.get('name', ''))
    return any(normalized(term) in title for term in spec.get('premium_terms', []) if str(term).strip())


def score(offer):
    # Prioriza reputação, vendas e desconto, com bônus controlado para marca/premium.
    from inteligencia_ofertas import brand_match
    return (12 * brand_match(offer) + 18 * bool(offer.get('radar_premium'))
            + 10 * (offer.get('official_store') is True)
            + 40 * offer['rating'] / 5
            + 35 * min(math.log10(1 + offer['sales']) / 4, 1)
            + 25 * min(offer['discount'], 60) / 60)


def select(groups, limit, preserve_order=False):
    selected, seen = [], set()
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


def specs_for_round(groups, themes, round_index=0, loop=False, forced_group=None):
    if forced_group:
        if forced_group not in groups:
            raise ValueError('Grupo inválido: ' + forced_group)
        target = forced_group
    elif loop:
        target = groups[round_index % len(groups)]
    else:
        return 'Todos', themes
    return target, [spec for spec in themes if spec['group'] == target]


def collect_theme(client, spec, collect):
    merged, scanned = {}, 0
    for index, query in enumerate(spec['queries']):
        offers, count = collect(
            client, str(query), pages=2,
            minimum_discount=spec['min_discount'],
            minimum_rating=spec['min_rating'],
            minimum_sales=spec['min_sales'],
            minimum_price=spec['min_price'])
        scanned += count
        for offer in offers:
            if pertence_ao_tema(spec['theme'], offer):
                enriched = dict(
                    offer,
                    tema_radar=spec['theme'],
                    radar_group=spec['group'],
                    radar_premium=premium_match(spec, offer))
                previous = merged.get(enriched['product_id'])
                if previous is None or score(enriched) > score(previous):
                    merged[enriched['product_id']] = enriched
        if index + 1 < len(spec['queries']):
            time.sleep(0.5)
    return list(merged.values()), scanned


def publish_one(client, ledger, offer, token, channel, send):
    key = offer['product_id']
    day = None
    try:
        prepared = client.prepare(offer)
        if offer.get('tema_radar') and not pertence_ao_tema(offer['tema_radar'], prepared):
            raise ValueError('Título revalidado não corresponde ao tema')
        if not prepared.get('api_image'):
            raise ValueError('Oferta sem imagem')
    except Exception:
        return 'revalidacao_falhou', None
    from inteligencia_ofertas import Intelligence
    intelligence = Intelligence(ledger.db)
    for key_name in ('tema_radar', 'radar_group', 'radar_premium'):
        if key_name in offer:
            prepared[key_name] = offer[key_name]
    prepared['history_badge'] = intelligence.badge(prepared, channel)
    day = ledger.reserve(key, offer=prepared, channel=channel)
    if day is None:
        return 'duplicada', None
    try:
        message_id, _ = send(token, channel, prepared, prepared['api_image'])
    except Exception:
        return 'incerta', None
    if message_id is None:
        ledger.release(key, day)
        return 'rejeitada', None
    try:
        ledger.finish(key, day, message_id, offer=prepared, channel=channel)
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
        parser.exit(1, 'Coloque este arquivo junto dos módulos do radar e instale requirements.txt.\n')
    base = Path(__file__).resolve().parent
    load_dotenv(base / '.env', encoding='utf-8-sig')
    try:
        catalog_groups, catalog_themes = load_catalog(base / CATALOG_FILE)
        active_group, specs = specs_for_round(
            catalog_groups, catalog_themes, args.round_index, args.loop, args.grupo)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.exit(1, f'Configuração do radar inválida: {error}\n')

    token, channel = os.getenv('TELEGRAM_TOKEN'), os.getenv('TELEGRAM_CANAL')
    if args.publicar and (not token or not channel):
        parser.exit(1, 'Faltam TELEGRAM_TOKEN/TELEGRAM_CANAL no .env local.\n')
    try:
        client = ShopeeAffiliate.from_env()
        interval = 600
    except Exception:
        parser.exit(1, 'Confira SHOPEE_APP_ID, SHOPEE_SECRET e INTERVALO_PUBLICACOES no .env.\n')

    if args.enfileirar:
        print('RADAR PARA FILA: o publicador unificado fará os envios.')
    else:
        print('PUBLICAÇÃO REAL. Destino:', channel) if args.publicar else print('PRÉVIA: nada será publicado.')
    print(f'Catálogo: {len(catalog_themes)} temas em {len(catalog_groups)} grupos; grupo da rodada: {active_group}.')
    print('Filtros de preço/desconto/nota/vendas são específicos por tema e revalidados antes da publicação.')
    print('Loja oficial só recebe bônus se a API fornecer um sinal explícito; nomes de loja não são usados como prova.')

    groups = []
    for spec in specs:
        theme = spec['theme']
        try:
            offers, scanned = collect_theme(client, spec, collect)
        except Exception:
            query = spec['queries'][0]
            parser.exit(1, f'Consulta falhou em {theme}. Nada foi enviado nesta rodada. Rode py radar_shopee.py --buscar "{query}" para diagnóstico.\n')
        print(
            f"{theme}: {scanned} consultados; {len(offers)} após filtros "
            f"(preço >= R$ {spec['min_price']}, desconto >= {spec['min_discount']}%).")
        groups.append((theme, offers))
        time.sleep(1)

    if args.enfileirar:
        from inteligencia_ofertas import Intelligence
        registry = Ledger(base / 'publicacoes.sqlite3')
        try:
            intelligence = Intelligence(registry.db)
            intelligence.enqueue([offer for _, offers in groups for offer in offers])
            print(f'Fila persistente: {len(intelligence.pending(channel))} ofertas elegíveis; validade de 2 horas.')
        finally:
            registry.db.close()
        return

    selected = select(groups, args.limite)
    if not selected:
        print('Nenhuma oferta elegível. Nenhum envio realizado; filtros preservados.')
        return
    print('\nSELEÇÃO:')
    for theme, offer in selected:
        prefix = 'a partir de ' if offer.get('price_from') else ''
        premium = ' | PREMIUM' if offer.get('radar_premium') else ''
        print(f"[{theme}] {offer['name']} | {prefix}R$ {offer['price']} | desconto informado {offer['discount']:g}% | nota {offer['rating']:g} | vendas {offer['sales']}{premium}")

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
    parser.add_argument('--enfileirar', action='store_true', help='Entrega ofertas ao publicador unificado, sem envio direto.')
    parser.add_argument('--loop', action='store_true', help='Mantém a consulta e publicação periódicas até Ctrl+C.')
    parser.add_argument('--intervalo', type=int, default=600, help='Segundos entre rodadas; padrão 600 (10 min).')
    parser.add_argument('--limite', type=int, default=1, choices=range(1, 4), help='Máximo por rodada; padrão 1.')
    parser.add_argument('--grupo', default=None, help='Força um grupo específico para diagnóstico/execução.')
    args = parser.parse_args()
    if args.publicar and args.enfileirar:
        parser.error('Escolha --publicar OU --enfileirar.')
    if args.intervalo < 600:
        parser.error('--intervalo deve ser pelo menos 600 segundos.')
    from execucao_unica import instancia_unica
    from contextlib import ExitStack
    with ExitStack() as stack:
        stack.enter_context(instancia_unica(Path(__file__).resolve().parent / 'radar.lock'))
        if args.publicar:
            stack.enter_context(instancia_unica(Path(__file__).resolve().parent / 'publicador.lock'))
        run_loop(args, parser)


def run_loop(args, parser):
    # Derivado do relógio para que reiniciar o processo não volte sempre ao primeiro grupo.
    args.round_index = int(time.time() // args.intervalo) if args.loop and not args.grupo else 0
    while True:
        started = time.monotonic()
        try:
            run_round(args, parser)
        except SystemExit:
            if not args.loop:
                raise
            print('Rodada sem sucesso; nova consulta no próximo intervalo.')
        except Exception:
            print('Falha operacional; detalhes sensíveis omitidos. Confira rede, arquivos e configuração.')
            if not args.loop:
                raise SystemExit(1)

        if args.enfileirar:
            try:
                from radar_kabum import production_round
                kabum = production_round(
                    Path(__file__).resolve().parent,
                    os.getenv('TELEGRAM_CANAL', ''),
                )
                print(
                    'KaBuM produção:',
                    kabum['valid'], 'produtos;',
                    kabum['changes'], 'preços alterados;',
                    len(kabum['candidates']), 'candidatas;',
                    kabum['pending'], 'produtos na fila;',
                    kabum.get('coupon_pending', 0), 'cupons oficiais na fila.'
                )
                for note in kabum['api_notes']:
                    print('KaBuM:', note)
            except Exception as error:
                print('KaBuM: rodada falhou sem interromper Shopee |', type(error).__name__)

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
