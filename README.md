# Radar de Ofertas

Automação em Python para monitorar ofertas e cupons, gerar links próprios de afiliado e publicar no Telegram com controle de prioridade, deduplicação e recuperação após reinício.

> Imagens de canais específicos podem usar **rebranding visual** configurado por `TG_REBRAND_CHATS`: o monitor cobre banner e selo de preço da origem antes de publicar. Consulte `docs/operacao.md`.

## Estado atual — 02/10/2026

| Componente | Estado |
|---|---|
| Monitor Telegram | Ativo; monitora grupos/canais configurados, mensagens novas, edições e recuperação de mensagens recentes |
| Canal especial em modo espelho | Ativo; preserva texto/formatação/imagem, remove links informativos e troca apenas o link do produto pelo afiliado |
| Shopee | API de afiliados integrada; radar contínuo com 43 temas |
| Intervalo do Radar Shopee | 1 publicação do radar a cada 1200 s (20 min); grupos/canais e cupons não aguardam esse relógio |
| Cupons Shopee | Ativo; mensagens exclusivas de cupons usam arte e padrão próprios |
| Mercado Livre | Ofertas de grupos monitorados e entrada manual integradas; geração automática de afiliado usa sessão do Link Builder |
| Cupons Mercado Livre | Ativo; listas exclusivas usam arte própria e o Social configurado |
| KaBuM | KaBuM 2.0 em produção via Awin: Product Feed, histórico, ranking, Link Builder, Offers API, cupons oficiais e publicação unificada no Telegram |
| Gate pré-publicação | Ativo; revalida identidade, link, preço/validade quando há fonte confiável, deduplicação e estoque explícito antes do envio |
| Amazon | Integração Creators API implementada em modo fail-closed; aguarda Partner Tag e credenciais OAuth da conta de Associados |
| Saúde operacional | `radar_health.py` verifica serviços, filas, backups, ML, estoque KaBuM e credenciais; na VPS roda a cada 5 min e alerta somente em mudança relevante |
| Instagram e WhatsApp | Fundação multicanal pronta e fail-closed; adaptadores de publicação ainda não ativados |

O fluxo operacional atual é iniciado por `INICIAR_INTEGRADO.bat`.

## Prioridade de publicação

1. ofertas novas captadas nos grupos/canais;
2. ofertas recuperadas após reinício;
3. cupons oficiais KaBuM elegíveis;
4. Radar automático Shopee/KaBuM.

As ofertas captadas não aguardam os 20 minutos do radar. Mensagens de produto que contêm um código de cupom continuam sendo uma única publicação; somente mensagens dedicadas exclusivamente a cupons entram no fluxo visual próprio.

## Gate central pré-publicação

`prepublicacao.py` roda imediatamente antes da reserva/envio. Ele bloqueia aumento de preço, faixa/valor ambíguo, link inválido, produto divergente, cupom expirado/inativo, indisponibilidade explícita e duplicação. Se uma fonte confiável comprovar preço menor, a publicação é atualizada para o novo valor antes do envio. Decisões são gravadas em `prepublication_gate` dentro de `publicacoes.sqlite3`; bloqueios não consomem o slot do radar.

A validação forte é usada no Radar Shopee (API), Mercado Livre automático (leitura pública), KaBuM (feed recente) e cupons KaBuM (Offers API). Ofertas de grupo Shopee com preço final condicionado a cupom/Pix não têm esse valor final exposto pela API; por isso só usam preço da origem por até 5 minutos, com identidade/link revalidados, sem afirmar uma confirmação de preço inexistente.

## Resiliência de envio Telegram

`telegram_api.py` classifica as respostas da Bot API antes de decidir o efeito na fila. Erros permanentes 4xx de uma publicação são rejeitados sem pausar as demais; `429` respeita `retry_after` e pausa globalmente; erros 5xx conhecidos aguardam nova tentativa apenas daquele item; `401/403/404` são tratados como falha de configuração e aplicam backoff global. Falha de rede ou resposta cujo resultado não possa ser comprovado vira `uncertain`, preservando a proteção contra duplicação.

Se o Telegram rejeitar especificamente a mídia de um `sendPhoto` com erro 400 de foto/imagem/arquivo, o publicador tenta uma única vez como `sendMessage`, sem imagem. Outros 400 — como HTML inválido — não recebem retry cego.

`publisher_backoff.py` mantém no `publicacoes.sqlite3` o backoff individual das falhas transitórias do publicador. A espera cresce progressivamente por oferta e motivo, até o teto de 30 minutos, sobrevive a reinícios e é zerada quando a origem muda ou a publicação é concluída. Condições que dependem de edição da origem, como preço ausente ou link estruturalmente inválido, não são reprocessadas em loop a cada segundo.

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

### Produção atual na VPS Linux

Na produção, o projeto fica em `/opt/radar` e os componentes principais são gerenciados pelo `systemd`:

- `radar-monitor.service` — captura Telegram;
- `radar-publicador.service` — publicação unificada;
- `radar-shopee.service` — radar contínuo Shopee.

O fluxo `.bat` continua documentado para execução local em Windows, mas não é o gerenciador da instância de produção. Os units e o script de backup usados pela VPS estão versionados em `deploy/`; a divisão entre entrypoints, núcleo compartilhado, integrações, utilitários e histórico está documentada em [docs/estrutura-repositorio.md](docs/estrutura-repositorio.md).

## Configuração

Copie `.env.example` para `.env` apenas em uma instalação nova. Em instalações existentes, preserve o `.env` atual.

A normalização, defaults e limites das opções operacionais ficam centralizados em `configuracao.py`. Os segredos continuam exclusivamente no `.env`. Para ver os valores efetivos **sem revelar credenciais**:

```bash
./.venv/bin/python configuracao.py
```

Principais grupos de configuração:

- Operação: `RADAR_INTERVALO_PUBLICACAO=1200`, `IDADE_MAXIMA_MINUTOS=120`, `IDADE_MAXIMA_RECUPERADAS_MINUTOS=45`, `INTERVALO_RECUPERADAS=30`, `TG_RECUPERAR_MINUTOS=30`, `TG_RECUPERAR_MAX_MENSAGENS=500` e `RADAR_DESTINOS_ATIVOS=telegram`;
- Telegram: `TELEGRAM_TOKEN`, `TELEGRAM_CANAL`, `TG_API_ID`, `TG_API_HASH`, `TG_CHATS`, `TG_PAUSED_CHATS`, `TG_ESPELHO_CHATS`;
- Shopee: `SHOPEE_APP_ID`, `SHOPEE_SECRET`, `SHOPEE_SUB_ID`;
- Mercado Livre: OAuth para ferramentas de desenvolvimento e sessão do Link Builder para geração de afiliado;
- KaBuM/Awin: `KABUM_AWIN_FEED_URL`, `AWIN_PUBLISHER_ID`, `AWIN_ACCESS_TOKEN` e demais opções descritas em [KABUM_AWIN.md](KABUM_AWIN.md);
- Amazon: `AMAZON_PARTNER_TAG`, `AMAZON_CREATORS_CREDENTIAL_ID` e `AMAZON_CREATORS_CREDENTIAL_SECRET`, descritos em [AMAZON_CREATORS.md](AMAZON_CREATORS.md).

### Pausa temporária de fontes Telegram

Mantenha a fonte cadastrada em `TG_CHATS` e, quando quiser suspendê-la sem perder a configuração, adicione seu ID ou `@username` a `TG_PAUSED_CHATS`. A pausa prevalece sobre mídia, modo espelho e rebranding. O comando `--diagnosticar` informa `Pausado: True` quando a origem está suspensa.

Para reativar, remova somente a origem de `TG_PAUSED_CHATS` e reinicie o monitor.

### Métricas de qualidade das fontes

O monitor e o publicador registram, de forma fail-open, o ciclo de cada mensagem Telegram em `publicacoes.sqlite3`. Os estados são `RECEBIDA`, `CAPTADA`, `AGUARDANDO`, `REJEITADA` e `PUBLICADA`; falhas de métricas nunca bloqueiam uma oferta.

Relatórios disponíveis:

```bash
python relatorio_fontes.py --periodo 24h
python relatorio_fontes.py --periodo 7d
python relatorio_fontes.py --periodo 30d
python relatorio_fontes.py --periodo total
```

O relatório mostra mensagens recebidas, captadas, publicadas, rejeitadas, aguardando, aproveitamento e principais causas por fonte. A contagem confiável começa após a ativação desse recurso; não é feita atribuição retroativa de publicação a mensagens antigas.

## Shopee

O radar contínuo lê `radar_categorias.json`, atualmente com 43 temas em 6 grupos. Os filtros mínimos, regras de título, marcas prioritárias, fila persistente e histórico estão documentados em [docs/radar-shopee.md](docs/radar-shopee.md).

O publicador gera um novo link com as credenciais do projeto; falha de conversão bloqueia a publicação. Não há fallback para link de afiliado de terceiros. O histórico comparável usa janelas de 15 em 15 dias: 15, 30, 45, ..., 180.

## Mercado Livre

Há três fluxos distintos:

- ofertas de canais monitorados: o produto é identificado e o publicador tenta gerar um novo link de afiliado;
- grupo manual: preserva o link que o operador já inseriu como seu próprio link;
- listas exclusivas de cupons: usam a arte própria e o texto padrão do projeto.

A sessão de afiliados do Link Builder possui saúde própria em SQLite: respostas 401/403 abrem circuit breaker apenas para o ML automático, registram o estado no `radar_health.py` e podem disparar alerta privado via `TELEGRAM_ADMIN_CHAT`. Mudança real de Cookie/CSRF é detectada por fingerprint e libera nova tentativa imediatamente após reinício.

A leitura pública automática é separada da sessão sensível de afiliados. Ela usa `ML_READER_COOKIE` opcional; quando vazio, HTTP/Playwright operam anonimamente e nunca reutilizam `ML_AFFILIATE_COOKIE`. A leitura pública também mantém backoff persistente, quarentena por link e circuit breaker próprios para sequências de HTTP 403.

Consulte [MERCADO_LIVRE_AFILIADOS.md](MERCADO_LIVRE_AFILIADOS.md), [MERCADO_LIVRE_AUTOMATICO.md](MERCADO_LIVRE_AUTOMATICO.md) e [PUBLICADOR_MERCADO_LIVRE.md](PUBLICADOR_MERCADO_LIVRE.md).

## KaBuM / Awin

`radar_kabum.py` participa do ciclo de produção. Ele atualiza o Product Feed, mantém histórico SQLite, detecta quedas comprovadas, usa Link Builder como fallback e consulta a Offers API. Produtos e cupons oficiais elegíveis entram na fila persistente e são enviados pelo publicador unificado, respeitando prioridade e deduplicação.

Cupons genéricos KaBuM usam `cupons_kabum.py` e `assets/banner_cupons_kabum.png`. Consulte [KABUM_AWIN.md](KABUM_AWIN.md).

## Amazon / Creators API

`amazon_afiliados.py` implementa a API oficial atual da Amazon Brasil com OAuth 2.0. O produto é identificado por ASIN e, quando as credenciais estiverem configuradas, preço, disponibilidade, imagem, título e o link afiliado são obtidos da Creators API. Sem credenciais o fluxo permanece aguardando e não publica. Consulte [AMAZON_CREATORS.md](AMAZON_CREATORS.md).

## Recuperação e persistência

O monitor reprocessa por padrão mensagens recentes ao iniciar. As filas ativas ficam em `publicacoes.sqlite3`: `captured_queue` recebe ofertas dos grupos/canais e `radar_queue` recebe os radares contínuos. A fila capturada substitui revisões antigas da mesma mensagem, expira entradas pela janela de publicação e remove itens enviados com sucesso. O antigo `fila_ofertas_v2.jsonl` é apenas uma fonte de migração compatível para instalações existentes. A tabela `deliveries` é a fundação aditiva da distribuição multicanal: nesta fase o Telegram continua controlado pelo ledger legado e cada envio confirmado/incerto é espelhado na nova estrutura com `external_id` textual.

Preserve especialmente:

- `.env`;
- `monitor_ofertas.session`;
- `publicacoes.sqlite3` e arquivos WAL/SHM associados;
- `monitor_recuperacao.json` e `imagens_ofertas/`;
- histórico/cache KaBuM local quando desejar preservar a linha de base.

### Reservas de publicação

O ledger diferencia três estados antes da confirmação: `reserved` (reservado, envio ainda não iniciado), `sending` (requisição ao Telegram iniciada) e `uncertain` (resultado do envio não pôde ser confirmado). Ao reiniciar o publicador, reservas `reserved` abandonadas são liberadas com segurança; estados `sending` do processo anterior viram `uncertain` e permanecem bloqueados para evitar duplicação.

Para listar ou reconciliar somente esses casos:

```bash
./.venv/bin/python reconciliar_reservas.py
./.venv/bin/python reconciliar_reservas.py --nao-enviado "PRODUCT_ID" --dia YYYY-MM-DD
./.venv/bin/python reconciliar_reservas.py --enviado "PRODUCT_ID" --dia YYYY-MM-DD --message-id 123
```

Nunca libere um `uncertain` sem conferir o canal quando houver dúvida sobre o resultado do envio. O diagnóstico `radar_health.py` também informa a quantidade desses estados.

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
- [Amazon Creators API](AMAZON_CREATORS.md)
- [Continuidade do projeto](CONTINUIDADE.md)