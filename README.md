# Radar de Ofertas

Automação em Python para monitorar ofertas e cupons, gerar links próprios de afiliado e publicar no Telegram com controle de prioridade, deduplicação e recuperação após reinício.

> Imagens de canais específicos podem usar **rebranding visual** configurado por `TG_REBRAND_CHATS`: o monitor cobre banner e selo de preço da origem antes de publicar. Consulte `docs/operacao.md`.

## Estado atual — 30/09/2026

| Componente | Estado |
|---|---|
| Monitor Telegram | Ativo; monitora grupos/canais configurados, mensagens novas, edições e recuperação de mensagens recentes |
| Canal especial em modo espelho | Ativo; preserva texto/formatação/imagem, remove links informativos e troca apenas o link do produto pelo afiliado |
| Shopee | API de afiliados integrada; radar contínuo com 43 temas |
| Intervalo do Radar Shopee | 1 publicação do radar a cada 1200 s (20 min); grupos/canais e cupons não aguardam esse relógio |
| Cupons Shopee | Ativo; mensagens exclusivas de cupons usam arte e padrão próprios |
| Mercado Livre | Ofertas de grupos monitorados e entrada manual integradas; geração automática de afiliado usa sessão do Link Builder |
| Cupons Mercado Livre | Ativo; listas exclusivas usam arte própria e o Social configurado |
| KaBuM | Integração Awin em modo diagnóstico; Product Feed, cache e histórico de preços validados localmente; ainda não publica no Telegram |
| Amazon | Pendente |
| Instagram e WhatsApp | Publicação multicanal pendente |

O fluxo operacional atual é iniciado por `INICIAR_INTEGRADO.bat`.

## Prioridade de publicação

1. ofertas novas captadas nos grupos/canais;
2. ofertas recuperadas após reinício;
3. Radar Shopee.

As ofertas captadas não aguardam os 20 minutos do radar. Mensagens de produto que contêm um código de cupom continuam sendo uma única publicação; somente mensagens dedicadas exclusivamente a cupons entram no fluxo visual próprio.

## Canal especial

O modo espelho é ativado por `TG_ESPELHO_CHATS`. A configuração validada em produção usa a ID numérica do Telegram:

```env
TG_ESPELHO_CHATS=-1003781163851
```

Nesse modo, links de redes sociais, páginas informativas e promoções auxiliares sem produto são descartados. Exemplo já tratado: uma oferta Shopee com um segundo link para `/m/shopeevip` mantém somente o link que resolve para o produto.

## Execução integrada

Instale as dependências:

```powershell
py -m pip install -r requirements.txt
```

Inicie:

```powershell
INICIAR_INTEGRADO.bat
```

Equivalente manual:

```powershell
py -u monitor_ofertas.py
py -u radar_shopee_continuo.py --enfileirar --loop --intervalo 1200 --limite 3
py -u bot_ofertas_revisao.py
```

Não execute outra instância do mesmo monitor, radar ou publicador em paralelo.

## Configuração

Copie `.env.example` para `.env` apenas em uma instalação nova. Em instalações existentes, preserve o `.env` atual.

Principais grupos de configuração:

- Telegram: `TELEGRAM_TOKEN`, `TELEGRAM_CANAL`, `TG_API_ID`, `TG_API_HASH`, `TG_CHATS`, `TG_ESPELHO_CHATS`;
- Shopee: `SHOPEE_APP_ID`, `SHOPEE_SECRET`, `SHOPEE_SUB_ID`;
- Mercado Livre: OAuth para ferramentas de desenvolvimento e sessão do Link Builder para geração de afiliado;
- KaBuM/Awin: `KABUM_AWIN_FEED_URL` e opções de seleção/cache descritas em [KABUM_AWIN.md](KABUM_AWIN.md).

## Shopee

O radar contínuo lê `radar_categorias.json`, atualmente com 43 temas em 6 grupos. Os filtros mínimos, regras de título, marcas prioritárias, fila persistente e histórico estão documentados em [docs/radar-shopee.md](docs/radar-shopee.md).

O publicador gera um novo link com as credenciais do projeto; falha de conversão bloqueia a publicação. Não há fallback para link de afiliado de terceiros.

## Mercado Livre

Há três fluxos distintos:

- ofertas de canais monitorados: o produto é identificado e o publicador tenta gerar um novo link de afiliado;
- grupo manual: preserva o link que o operador já inseriu como seu próprio link;
- listas exclusivas de cupons: usam a arte própria e o texto padrão do projeto.

Consulte [MERCADO_LIVRE_AFILIADOS.md](MERCADO_LIVRE_AFILIADOS.md), [MERCADO_LIVRE_AUTOMATICO.md](MERCADO_LIVRE_AUTOMATICO.md) e [PUBLICADOR_MERCADO_LIVRE.md](PUBLICADOR_MERCADO_LIVRE.md).

## KaBuM / Awin

`radar_kabum.py` aceita Product Feed direto ou a Product Feed List da Awin. A versão atual reconhece o feed KaBuM, prioriza Feed ID 46967/Advertiser 17729 por padrão, mantém cache local e histórico SQLite e detecta quedas comprovadas de preço.

O módulo permanece seguro por padrão: **não publica no Telegram**. Consulte [KABUM_AWIN.md](KABUM_AWIN.md).

## Recuperação e persistência

O monitor reprocessa por padrão mensagens recentes ao iniciar. A fila e o banco local preservam a continuidade e evitam rajadas/repetições. Preserve especialmente:

- `.env`;
- `monitor_ofertas.session`;
- `publicacoes.sqlite3` e arquivos WAL/SHM associados;
- filas locais e `imagens_ofertas/`;
- histórico/cache KaBuM local quando desejar preservar a linha de base.

## Testes

```powershell
py -m unittest discover -p "test_*.py" -v
```

O GitHub Actions executa testes com serviços simulados e sem credenciais de produção. Testes locais com APIs reais continuam necessários para validar autenticação e disponibilidade externa.

## Segurança

Trate o repositório como potencialmente público. Nunca versione `.env`, sessão Telegram, cookies, CSRF, tokens, URLs privadas de feed, bancos, filas ou arquivos de catálogo baixados. O `.gitignore` cobre os artefatos operacionais conhecidos.

## Documentação

- [Operação atual](docs/operacao.md)
- [Arquitetura](docs/arquitetura.md)
- [Radar Shopee](docs/radar-shopee.md)
- [Integração de grupos/cupons](INTEGRACAO.md)
- [KaBuM/Awin](KABUM_AWIN.md)
- [Continuidade do projeto](CONTINUIDADE.md)
