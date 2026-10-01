# KaBuM / Awin — Product Data Feed

## Estado

Integração validada localmente em 30/09/2026 em **modo diagnóstico**.

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
- **não publica no Telegram**.

Na validação local mais recente, a lista Awin selecionou o feed KaBuM BR/Portuguese, o catálogo retornou 4.953 linhas e todas as 4.953 foram registradas como produtos válidos.

## Configuração

No `.env`:

```env
KABUM_AWIN_FEED_URL=
KABUM_AWIN_ADVERTISER_ID=17729
KABUM_AWIN_FEED_ID=46967
KABUM_AWIN_LANGUAGE=pt_BR
KABUM_MIN_QUEDA_PCT=5
KABUM_FEED_CACHE=kabum_feed_atual.csv.gz
KABUM_DOWNLOAD_TIMEOUT=60
KABUM_DOWNLOAD_TENTATIVAS=3
KABUM_FEED_DELIMITADOR=
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

O feed atual não é usado pelo projeto para afirmar automaticamente “promoção do dia”. O módulo também não publica ranking no Telegram nesta fase.

## Próxima etapa

Antes de publicar ofertas KaBuM, o projeto ainda precisa definir e testar:

1. ranking de relevância;
2. regras de categoria/marca;
3. intervalo e prioridade em relação aos demais canais;
4. deduplicação com o histórico de publicações;
5. mensagem/padrão visual;
6. critérios para promoções temporárias quando houver evidência confiável no feed.

A publicação deve permanecer desligada até essas regras estarem consolidadas.

## Integração com grupos monitorados — Passo 1

Implementado em 01/10/2026.

- `ofertas_core.py` reconhece URLs `https://www.kabum.com.br/produto/ID/...` pela identidade estável `KaBuM:ID`;
- `monitor_ofertas.py` aceita KaBuM entre as lojas de produto e enfileira a oferta dos grupos monitorados;
- `kabum_afiliados.py` consulta `kabum_historico.sqlite3` pelo `merchant_product_id`;
- o link publicado vem exclusivamente de `affiliate_url` / `aw_deep_link` do Product Feed Awin;
- o link Awin é validado como `https://www.awin1.com/pclick.php` do anunciante KaBuM (`m=17729`);
- canais em modo espelho também aceitam produto KaBuM e substituem somente o link da loja;
- se o ID KaBuM não existir no feed atual, a oferta fica aguardando e **não** usa o link comum como fallback;
- o Link Builder da Awin ainda não faz parte deste passo.

`radar_kabum.py` continua em modo diagnóstico: esta etapa habilita somente ofertas KaBuM originadas nos grupos monitorados.
