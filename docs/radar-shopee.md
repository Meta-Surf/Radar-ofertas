# Radar direto da Shopee

Busca produtos pela API de Afiliados usando o mesmo `SHOPEE_APP_ID` e `SHOPEE_SECRET` do `.env`. Não depende da conta Telegram nem de mensagens de grupos para descobrir ofertas. O publicador ainda precisa do token e canal Telegram.

## Primeiro teste, sem publicação

Atualize os arquivos do projeto e mantenha seu `.env` e banco locais. Execute na raiz:

```powershell
py radar_shopee.py --buscar "fone bluetooth"
```

Esse comando apenas consulta e mostra resultados: não gera links nem altera a fila. Por padrão, consulta até duas páginas de 20 resultados ordenadas por vendas, filtra desconto informado de pelo menos 20%, nota 4,5 e 50 vendas. Exige imagem e período de oferta ativo explicitamente informado. Preços com variações aparecem como “a partir de”.

Sem resultados, tente outra busca ou ajuste conscientemente os critérios:

```powershell
py radar_shopee.py --buscar "fone bluetooth" --desconto-min 10 --nota-min 4 --vendas-min 10
```

## Preparar ofertas e simular

Pare o publicador real com Ctrl+C antes de enfileirar: se estiver ativo, ele pode publicar assim que a fila mudar.

```powershell
py radar_shopee.py --buscar "fone bluetooth" --enfileirar
py bot_ofertas_revisao.py --simular
```

O radar substitui somente `fila_shopee_api.jsonl`, inclusive por uma fila vazia quando não há candidatos. Não altera a fila do monitor Telegram. A simulação reconsulta os produtos, gera seus links afiliados e mostra a mensagem sem publicar. Ela pode mostrar produtos já publicados, pois não reserva o registro diário.

## Funcionamento automático

Na primeira janela:

```powershell
py radar_shopee.py --buscar "fone bluetooth" --enfileirar --loop
```

Na segunda janela:

```powershell
py bot_ofertas_revisao.py
```

Repete a consulta a cada 15 minutos; `--intervalo` aceita segundos a partir de 300. Execute apenas uma instância do radar direto. O monitor dos grupos é opcional e pode continuar com sua própria fila. O publicador compartilha `publicacoes.sqlite3` para bloquear o mesmo produto no mesmo dia, independentemente da origem.

Antes de gerar o link, o publicador reconsulta preço, imagem, datas e filtros. Se não conseguir confirmar o produto elegível, não publica. Erros da API no radar limpam a fila direta e encerram o processo com mensagem para diagnóstico. Após resolver a falha, reinicie o radar.

## Campanhas e cupons

```powershell
py radar_shopee.py --campanhas
```

Lista nomes de campanhas com período ativo na primeira página (até 20). Não enfileira campanhas nem gera anúncios de cupons. O esquema oficial consultado não oferece nesses resultados código de cupom, saldo de resgates ou regras completas de elegibilidade. Portanto, a descoberta e validação automáticas de cupons continuam pendentes. Os cupons explicitamente captados do Telegram continuam com o comportamento anterior, sem garantia de validade.

## Limites

O desconto é o percentual informado pela Shopee; não comprova menor preço histórico. O radar não cobre todo o catálogo, não verifica estoque por variação e não confirma o preço final do carrinho ou frete. A validade do período de afiliados não garante estoque. Cupons não são presumidos nem descontados do preço.

As consultas seguem `productOfferV2` e `shopeeOfferV2` do [Explorer oficial](https://open-api.affiliate.shopee.com.br/explorer/v2), conferido em 25/09/2026. A validação automatizada usa respostas simuladas; o acesso real a essas operações deve ser testado no computador com suas credenciais.
