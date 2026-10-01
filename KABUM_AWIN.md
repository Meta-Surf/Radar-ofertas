# KaBuM / Awin — Product Data Feed

## Estado

Integração validada e colocada em produção em 01/10/2026. A execução manual sem `--enfileirar` continua diagnóstica.

A versão atual de `radar_kabum.py`:

- aceita URL de Product Feed direto;
- aceita a Product Feed List da Awin;
- localiza automaticamente o feed KaBuM;
- prioriza Advertiser ID `17729` e Feed ID `46967` por padrão;
- detecta delimitador;
- aceita CSV, GZIP e ZIP;
- mantém cache local;
- registra produtos e histórico de preços em SQLite;
- informa produtos novos e preços alterados;
- detecta quedas comprovadas pelo próprio histórico;
- em execução manual permanece diagnóstico por padrão; em produção, `--enfileirar` entrega candidatas ao publicador unificado real.

Na validação mais recente na VPS, o catálogo KaBuM/Awin contém 4.992 produtos e 5.442 registros históricos de preço.

## Configuração

No `.env`:

```env
KABUM_AWIN_FEED_URL=
KABUM_AWIN_ADVERTISER_ID=17729
KABUM_AWIN_FEED_ID=46967
KABUM_AWIN_LANGUAGE=pt_BR
KABUM_MIN_QUEDA_PCT=5
KABUM_QUEUE_LIMIT=20
KABUM_CANDIDATE_MAX_AGE_HOURS=24
KABUM_FEED_CACHE=kabum_feed_atual.csv.gz
KABUM_DOWNLOAD_TIMEOUT=60
KABUM_DOWNLOAD_TENTATIVAS=3
KABUM_FEED_DELIMITADOR=
KABUM_ENHANCED_LOCALE=pt_BR
AWIN_PUBLISHER_ID=
AWIN_ACCESS_TOKEN=
AWIN_API_TIMEOUT=30
```

`KABUM_AWIN_FEED_URL` é privada e não deve ser versionada.

`KABUM_FEED_DELIMITADOR` normalmente deve ficar vazio. Use apenas quando precisar forçar `,`, `;`, `|` ou `TAB`.

## Execução

Usando o `.env`:

```powershell
py radar_kabum.py
```

Arquivo local:

```powershell
py radar_kabum.py --arquivo "feed.csv.gz"
```

URL informada na CLI:

```powershell
py radar_kabum.py --url "URL_PRIVADA"
```

Alterar o limiar de queda:

```powershell
py radar_kabum.py --queda-min 8 --limite 20
```

Desativar fallback para cache/feed local:

```powershell
py radar_kabum.py --sem-fallback
```

## Cache e histórico

Arquivos locais:

- `kabum_feed_atual.csv.gz`: último Product Feed válido;
- `kabum_historico.sqlite3`: catálogo atual e observações de preço.

O cache só é substituído depois de o conteúdo ser reconhecido como Product Feed compatível.

Quando o download falha, o script pode usar um cache válido ou um feed manual existente, salvo quando `--sem-fallback` é usado.

## Critério de queda

Uma queda só aparece quando existe uma observação histórica anterior maior que o preço atual. A primeira coleta cria a linha de base e não deve ser tratada como promoção por si só.

O feed atual não é usado para afirmar automaticamente “promoção do dia”. A produção publica apenas candidatas que passam pelos critérios de queda, ranking, idade, deduplicação e disponibilidade conhecida.

## Integração com grupos monitorados — Passo 1

Implementado em 01/10/2026.

- `ofertas_core.py` reconhece URLs `https://www.kabum.com.br/produto/ID/...` pela identidade estável `KaBuM:ID`;
- `monitor_ofertas.py` aceita KaBuM entre as lojas de produto e enfileira a oferta dos grupos monitorados;
- `kabum_afiliados.py` consulta `kabum_historico.sqlite3` pelo `merchant_product_id`;
- o link publicado vem exclusivamente de `affiliate_url` / `aw_deep_link` do Product Feed Awin;
- o link Awin é validado como `https://www.awin1.com/pclick.php` do anunciante KaBuM (`m=17729`);
- canais em modo espelho também aceitam produto KaBuM e substituem somente o link da loja;
- se o ID KaBuM não existir no feed atual, a oferta só usa fallback quando o Link Builder oficial da Awin estiver configurado; caso contrário fica aguardando;
- o link comum KaBuM nunca é tratado como link afiliado.

## KaBuM 2.0 — produção

Implementado em 01/10/2026 com publicação pela fila unificada.

- o serviço de radar existente executa a rodada KaBuM a cada 20 minutos;
- somente quedas de preço comprovadas e recentes entram como candidatas (padrão: >= 5% e até 24h);
- produtos novos criam linha de base, mas não são tratados como promoção;
- o histórico usa janelas comprovadas de 15 em 15 dias: 15, 30, 45, ..., 180;
- só é exibida a maior janela comprovada para o preço atual;
- ranking KaBuM combina queda, histórico, marcas prioritárias, categorias prioritárias, promoção oficial e estoque confirmado;
- JBL, NVIDIA e AMD recebem prioridade adicional;
- categorias de acessórios como suportes/cabos/adaptadores não recebem bônus por palavras como TV/notebook;
- estoque explicitamente indisponível é bloqueado; estoque vazio é tratado como desconhecido, nunca como confirmação;
- `publicacoes.sqlite3` controla deduplicação e republicação apenas com preço menor após 24h;
- ofertas dos grupos continuam com prioridade sobre as ofertas automáticas;
- o publicador central aplica o intervalo do radar também à KaBuM.

### APIs Awin opcionais

Com `AWIN_PUBLISHER_ID` e `AWIN_ACCESS_TOKEN` configurados:

- Link Builder gera tracking link quando o produto de grupo não consta no Product Feed;
- Offers API consulta apenas ofertas ativas da KaBuM (`advertiserId=17729`, `membership=joined`, região BR);
- vouchers só são associados quando a oferta oficial aponta diretamente para o mesmo produto;
- Enhanced Feed fornece `availability`; `out_of_stock` bloqueia a candidata.

Sem essas credenciais, a produção pelo Product Feed continua funcionando normalmente e os recursos opcionais permanecem desligados.
