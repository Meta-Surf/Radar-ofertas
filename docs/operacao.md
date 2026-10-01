# Operação atual — 01/10/2026

Este é o guia principal para colocar o Radar de Ofertas em execução. Documentos datados de correções anteriores permanecem apenas como histórico.

## Requisitos

- VPS Linux para a produção atual ou Windows para execução local;
- Python 3.11+ (a VPS atual usa Python 3.12);
- dependências de `requirements.txt`;
- credenciais locais em `.env`;
- sessão Telegram autorizada;
- Chromium do Playwright quando usar a leitura automática do Mercado Livre.

Instalação:

```powershell
py -m pip install -r requirements.txt
py -m playwright install chromium
```

## Produção atual — VPS Linux

A instância de produção fica em `/opt/radar` e usa serviços `systemd` separados:

```bash
systemctl status radar-monitor.service
systemctl status radar-publicador.service
systemctl status radar-shopee.service
```

Logs do monitor:

```bash
journalctl -u radar-monitor.service -n 100 --no-pager
```

Ao alterar somente fontes Telegram, reinicie apenas `radar-monitor.service`; o publicador e o Radar Shopee não precisam ser interrompidos.

## Início integrado local — Windows

Execute apenas uma vez:

```powershell
INICIAR_INTEGRADO.bat
```

Ele abre:

1. monitor dos grupos/canais;
2. Radar Shopee em modo de fila;
3. publicador unificado.

Configuração equivalente:

```powershell
py -u monitor_ofertas.py
py -u radar_shopee_continuo.py --enfileirar --loop --intervalo 1200 --limite 3
py -u bot_ofertas_revisao.py
```

O Radar Shopee pode gerar candidatos com frequência, mas o publicador libera no máximo uma tentativa de publicação do radar a cada **20 minutos**. Ofertas captadas dos canais não aguardam esse relógio.

## Prioridade

A ordem atual é:

1. ofertas novas captadas ao vivo;
2. ofertas recuperadas após reinício;
3. cupons oficiais KaBuM elegíveis;
4. Radar automático Shopee/KaBuM.

As recuperadas usam `INTERVALO_RECUPERADAS` e não travam permanentemente uma oferta nova ao vivo.

## Recuperação automática

Ao iniciar, o monitor consulta mensagens recentes conforme:

```env
TG_RECUPERAR_MINUTOS=30
TG_RECUPERAR_MAX_MENSAGENS=500
INTERVALO_RECUPERADAS=30
IDADE_MAXIMA_RECUPERADAS_MINUTOS=45
```

A recuperação reconstrói álbuns, considera edições e usa digest para evitar gravar a mesma revisão repetidamente. O banco de publicações continua sendo a barreira final contra duplicação.

## Pausar grupos/canais sem apagar a configuração

`TG_CHATS` continua sendo o cadastro das fontes. Para suspender temporariamente uma origem, mantenha-a em `TG_CHATS` e adicione seu ID numérico ou `@username` a:

```env
TG_PAUSED_CHATS=
```

A pausa tem precedência sobre captura normal, mídia, modo espelho e rebranding. Assim, histórico e configuração permanecem disponíveis para reativação futura. Para reativar uma fonte, retire apenas o valor de `TG_PAUSED_CHATS` e reinicie o monitor.

No diagnóstico de uma mensagem, o monitor exibe `Pausado: True` quando a origem estiver suspensa.

## Métricas de qualidade por fonte

As métricas usam o próprio `publicacoes.sqlite3`, portanto entram no backup operacional já existente. Cada mensagem de origem é identificada por `chat_id + message_id` e mantém estado atual, motivo, loja/produto quando conhecidos, presença de preço/imagem e publicação final.

Estados: `RECEBIDA`, `CAPTADA`, `AGUARDANDO`, `REJEITADA`, `PUBLICADA`.

Motivos comuns incluem `PRECO_AUSENTE`, `SEM_IMAGEM`, `SEM_PRODUTO`, `MULTIPLOS_PRODUTOS`, `LINK_NAO_RESOLVIDO`, `REDIRECIONADOR_BLOQUEADO`, `AFILIADO_INDISPONIVEL`, `AFILIADO_FALHOU`, `LEITURA_ML`, `EXPIRADA` e `DUPLICADA`.

A instrumentação é **fail-open**: erro ao gravar métrica gera aviso, mas não muda a decisão de captura/publicação.

Relatórios:

```bash
cd /opt/radar
./.venv/bin/python relatorio_fontes.py --periodo 24h
./.venv/bin/python relatorio_fontes.py --periodo 7d
./.venv/bin/python relatorio_fontes.py --periodo 30d
./.venv/bin/python relatorio_fontes.py --periodo total
```

A coluna de aproveitamento é `mensagens publicadas / mensagens recebidas`. A atribuição confiável começa a partir da ativação do recurso; não se tenta adivinhar retroativamente qual fonte merece crédito por publicações antigas.

## Gate central pré-publicação

O publicador executa `prepublicacao.py` imediatamente antes da reserva no ledger e antes de iniciar qualquer relógio de publicação.

Configuração padrão:

```env
GATE_SOURCE_PRICE_MAX_AGE_SECONDS=300
GATE_KABUM_FEED_MAX_AGE_SECONDS=1800
```

O primeiro valor limita por quanto tempo um preço explicitamente informado por uma origem pode ser aceito quando a loja não oferece revalidação forte daquele preço final. O segundo limita a idade máxima do Product Feed KaBuM usado para confirmar preço.

Decisões do Gate ficam em `publicacoes.sqlite3`, tabela `prepublication_gate`. Para ver os motivos mais recentes:

```bash
cd /opt/radar
./.venv/bin/python - <<'PY'
import sqlite3
db=sqlite3.connect("publicacoes.sqlite3")
for row in db.execute("""
SELECT datetime(checked,'unixepoch'),product,status,reason,old_price,new_price
FROM prepublication_gate ORDER BY id DESC LIMIT 30
"""):
    print(row)
db.close()
PY
```

Bloqueios comuns: `PRECO_AUMENTOU`, `PRECO_VARIANTE_AMBIGUO`, `PRECO_NAO_REVALIDADO`, `CUPOM_EXPIRADO`, `CUPOM_INATIVO`, `LINK_INVALIDO`, `DUPLICADA`, `PRODUTO_INCONSISTENTE`, `SEM_ESTOQUE_COMPROVADO` e `VALIDACAO_INDISPONIVEL`.

Uma oferta bloqueada pelo Gate não chama `Ledger.mark_attempt()`; o publicador continua para a próxima candidata sem consumir o intervalo de 20 minutos.

## Canal especial em modo espelho

Use a ID numérica no `.env`:

```env
TG_ESPELHO_CHATS=-1003781163851
```

Regras:

- preservar texto, emojis, formatação, preço, parcelas/condições e imagem da oferta;
- substituir somente o link do produto pelo link próprio de afiliado;
- remover redes sociais, páginas informativas e links auxiliares sem produto;
- se houver código de cupom dentro da oferta, mantê-lo na mesma publicação;
- somente mensagens exclusivas de cupons usam a arte própria.

Diagnóstico:

```powershell
py monitor_ofertas.py --diagnosticar https://t.me/NOME_DO_CANAL/NUMERO
```

O resultado deve indicar `Modo espelho: True`.

## Rebranding visual de imagens

Alguns canais de origem imprimem a própria marca e um valor dentro da foto. Para essas origens, configure o ID numérico em:

```env
TG_REBRAND_CHATS=
```

O monitor baixa a imagem autorizada, cobre a faixa superior com o banner **RADAR DE OFERTAS** e cobre o selo de preço da origem com **preço atual na mensagem**. O preço não é reimpresso na imagem: a legenda continua sendo a fonte do valor publicado e evita divergência quando a arte do terceiro estiver desatualizada.

Os padrões atuais foram ajustados para o layout do canal **Desconto em Games**:

```env
TG_REBRAND_TOP_RATIO=0.19
TG_REBRAND_PRICE_BOX=0.64,0.77,0.98,0.95
```

Se o rebranding falhar, a imagem original desse canal não é publicada. Com `EXIGIR_IMAGEM=1`, a oferta fica aguardando uma imagem tratada em vez de vazar a marca/preço da origem.

Para descobrir o ID correto:

```powershell
py monitor_ofertas.py --listar
```

Depois de configurar, `--diagnosticar` deve mostrar `Rebranding visual: True` para uma mensagem do canal.

## Shopee

Credenciais:

```env
SHOPEE_APP_ID=
SHOPEE_SECRET=
SHOPEE_SUB_ID=telegram
```

Teste de link sem publicar:

```powershell
py shopee_afiliados.py --testar "URL_DO_PRODUTO"
```

O radar contínuo possui 43 temas. O publicador não deve cair para link original quando a geração do afiliado falhar.

## Cupons Shopee

Somente mensagens dedicadas a cupons viram alerta de cupom. Uma oferta de produto que contenha `com cupom: CODIGO` continua sendo uma única oferta.

A arte é restaurada a partir de `assets/banner_cupons.parts/` quando necessário.

Teste isolado:

```powershell
py cupons_shopee.py --testar "URL_DO_CUPOM"
```

## Mercado Livre

### Ofertas de canais monitorados

O monitor identifica o produto, a leitura automática pode completar campos ausentes e `mercadolivre_afiliados.py` tenta gerar um novo link pela sessão do Link Builder.

Nunca use parcela como preço total. Se título/preço/imagem/produto não puderem ser confirmados dentro das regras atuais, a oferta fica pendente ou é bloqueada.

A leitura pública usa `mercadolivre_resiliencia.py`: falhas recebem backoff persistente; links com falhas repetidas entram em quarentena; e 5 respostas HTTP 403 distintas em até 5 minutos abrem um circuit breaker de 15 minutos. Isso evita repetir Playwright/HTTP contra páginas que o Mercado Livre está bloqueando.

### Grupo manual

`ML_MANUAL_CHAT` define a entrada manual. Nesse fluxo, o link inserido pelo operador é preservado, pois já deve ser o seu próprio link.

### Cupons

Listas exclusivas de cupons usam a arte Mercado Livre e o Social configurado em `ML_CUPONS_SOCIAL_URL`. Links promocionais da origem não são reaproveitados.

### Sessão de afiliado

Mantenha somente no `.env`:

```env
ML_AFFILIATE_COOKIE=
ML_AFFILIATE_CSRF=
ML_AFFILIATE_TAG=
ML_AFFILIATE_REFRESH_COOKIES=1
```

Esses valores não pertencem ao GitHub.

## KaBuM / Awin

Na VPS, o KaBuM 2.0 participa do ciclo do serviço `radar-shopee.service` e entrega ofertas/cupons ao publicador unificado. A execução manual sem `--enfileirar` continua disponível para diagnóstico.

Execute:

```powershell
py radar_kabum.py
```

Ele aceita `KABUM_AWIN_FEED_URL`, reconhece Product Feed List ou Product Feed, mantém cache/histórico e mostra quedas comprovadas. Com `AWIN_PUBLISHER_ID` e `AWIN_ACCESS_TOKEN`, também usa Link Builder e Offers API. Cupons oficiais genéricos elegíveis usam arte própria. Consulte [../KABUM_AWIN.md](../KABUM_AWIN.md).

O feed KaBuM disponível na conta não fornece estoque e o Enhanced Feed não está disponível; portanto estoque vazio continua sendo `DESCONHECIDO`, nunca `EM ESTOQUE`.

## Amazon / Creators API

A integração está pronta em `amazon_afiliados.py`, mas só entra em produção quando existirem no `.env`:

```env
AMAZON_PARTNER_TAG=
AMAZON_CREATORS_CREDENTIAL_ID=
AMAZON_CREATORS_CREDENTIAL_SECRET=
AMAZON_CREATORS_TIMEOUT=30
```

Sem essas credenciais, ofertas Amazon ficam aguardando. Com credenciais válidas, preço, disponibilidade, título, imagem e link afiliado vêm da Creators API oficial e ainda passam pelo Gate. Consulte [../AMAZON_CREATORS.md](../AMAZON_CREATORS.md).

## Saúde operacional

```bash
cd /opt/radar
./.venv/bin/python radar_health.py
./.venv/bin/python radar_health.py --json
./.venv/bin/python radar_health.py --alert
```

`--alert` só envia quando `TELEGRAM_ADMIN_CHAT` estiver configurado. O script verifica serviços, filas, backups, ML, cobertura de estoque KaBuM e estado das credenciais.

## Dados locais que devem ser preservados

Nunca substitua ou apague sem backup:

- `.env`;
- `monitor_ofertas.session`;
- `publicacoes.sqlite3` e WAL/SHM;
- `fila_ofertas_v2.jsonl` e demais filas;
- `monitor_recuperacao.json`;
- `imagens_ofertas/`;
- `kabum_historico.sqlite3`;
- `kabum_feed_atual.csv.gz`.

## Diagnóstico geral

Listar chats:

```powershell
py monitor_ofertas.py --listar
```

Simular o publicador:

```powershell
py bot_ofertas_revisao.py --simular
```

Rodar testes:

```powershell
py -m unittest discover -p "test_*.py" -v
```

## Sinais esperados no console

- monitor: `Monitorando ... chats`, `Oferta captada`, `Oferta espelho captada`;
- radar: atualização/enfileiramento de candidatos;
- publicador: `Publicado: ... | origem: telegram` ou `origem: radar`;
- KaBuM: produtos válidos, preços alterados, candidatos, produtos/cupons na fila e estado das APIs Awin.

## Regras de segurança

- não publique credenciais em logs, prints ou commits;
- não versionar sessão Telegram, cookies, tokens ou feed privado;
- não abrir duas instâncias do mesmo componente;
- não apagar o banco para forçar republicação;
- não publicar dados estimados quando a fonte não comprovar o valor.