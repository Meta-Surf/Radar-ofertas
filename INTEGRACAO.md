> Atualização de 27/09/2026: o fluxo atual usa **INICIAR_INTEGRADO.bat**, com grupos/cupons prioritários, radar a cada 600 segundos, 24 temas, marcas preferenciais e fila SQLite de duas horas. Preços divulgados passam a ser registrados após envio confirmado. Os selos de 30/60/90/180 dias exigem variante confirmada, ainda não fornecida pelas fontes atuais. Veja [ATUALIZACAO_RADAR.md](ATUALIZACAO_RADAR.md) para instalação e limites. Orientações históricas divergentes abaixo não descrevem o fluxo integrado atual.

# Cupons + grupos + radar Shopee

## Correção de preços captados nos grupos

O monitor reconhece valores como `💵 R$ 2391 no app`, `R$ 2.391,90 no Pix`,
`Preço: R$ 2391` e `De R$ 3000 por R$ 2391`. Preserva a condição junto ao
preço, incluindo `no app`, `à vista` e cupom indicado na mesma linha.
Não calcula preços a partir de parcelas ou descontos, nem escolhe entre
vários valores ambíguos. O preço continua sendo o informado no grupo;
não é uma verificação do checkout. O console agora mostra o preço captado.

Para aplicar esta correção, pare os processos antigos, substitua
`ofertas_core.py` e `monitor_ofertas.py` pelos arquivos atualizados e reinicie
`INICIAR_INTEGRADO.bat`. Preserve `.env`, a sessão e o banco existentes.
A correção vale para novas capturas: registros antigos já gravados sem preço
não contêm o texto original necessário para recuperá-lo, e mensagens já
publicadas no canal não são editadas automaticamente.

## Alertas de cupons

O monitor captura também mensagens novas cujo título indica cupom/cupons e
links explicitamente rotulados como páginas para resgatar cupons. Cada aviso
recebe texto próprio e até seis botões, um por link. Condições textuais junto
ao link, como `R$ 10 OFF em R$ 119`, são preservadas; nomes e chamadas para
grupos de terceiros não são copiados. Valores existentes somente na imagem
não são inferidos. Links de produtos identificados não são tratados como cupons.

O publicador resolve cada destino e pede um novo link à API com as credenciais
e o `SHOPEE_SUB_ID` do seu `.env`. Se algum destino não for resolvido, estiver
fora dos domínios permitidos ou a conversão falhar, o aviso não é publicado.
Não há fallback para links do afiliado original. O encurtador `desconto.games`,
presente no exemplo, só é aceito quando revela um destino Shopee por HTTP.
Páginas que exigem JavaScript ou bloqueiam a resolução ficam pendentes.

Cupons entram na mesma prioridade das ofertas dos grupos, sem aguardar o
relógio de 10 minutos do radar. A identidade do alerta usa destinos resolvidos,
condições e data de origem; avisos iguais no mesmo dia não são reenviados,
inclusive após reiniciar. Os limites de espera pedidos pelo Telegram continuam
valendo para todos. Alertas com condições ou datas diferentes são novos avisos.

### Banner aprovado

Salve a imagem aprovada em `assets/banner_cupons.png`, criando a pasta `assets`
se necessário. Ou configure `CUPONS_BANNER` no `.env` com o caminho da imagem.
Essa imagem ainda precisa ser reenviada pelo usuário e não acompanha este
pacote. O bot não reutiliza as imagens dos grupos nos alertas. Sem o arquivo,
envia texto e botões. Se o texto exceder a legenda de uma foto, envia texto
completo e botões para não cortar condições.

### Diagnóstico sem publicação

Execute na pasta do bot com seu `.env` existente:

```powershell
py cupons_shopee.py --testar "https://s.shopee.com.br/387hsEM3Dd" "https://s.shopee.com.br/5LCCSDMbNx"
```

Também aceita mais de um link e o encurtador `desconto.games`. O diagnóstico
consulta a Shopee, mas não escreve filas nem publica no Telegram. Para uma
prévia dos avisos já captados use `py bot_ofertas_revisao.py --simular`.
Sucesso nos testes locais não comprova que a API aceita cada cupom real;
essa consulta precisa ser feita com a conta configurada no computador do bot.

## Instalação

1. Pare as janelas antigas do radar, monitor e publicador com Ctrl+C.
2. Faça backup da pasta e extraia os arquivos deste pacote na mesma pasta do bot.
3. Preserve `.env`, `monitor_ofertas.session`, `publicacoes.sqlite3` e `imagens_ofertas`. Nenhum deles acompanha este pacote.
4. Instale as dependências com `py -m pip install -r requirements.txt`.
5. Abra `INICIAR_INTEGRADO.bat`. Ele abre três janelas, que devem ficar abertas.

Se preferir iniciar manualmente, execute cada comando em uma janela na mesma pasta:

```powershell
py -u monitor_ofertas.py
py -u radar_shopee_continuo.py --enfileirar --loop --intervalo 600 --limite 3
py -u bot_ofertas_revisao.py
```

O último comando publica de verdade no `TELEGRAM_CANAL` do `.env`. Para conferir as filas sem enviar, use `py bot_ofertas_revisao.py --simular` antes de iniciar o publicador real.

## Configuração existente

O monitor usa `TG_API_ID`, `TG_API_HASH` e `TG_CHATS`. Se a sessão ainda não estiver autenticada, siga o pedido de login na janela do monitor. `py monitor_ofertas.py --listar` mostra os chats disponíveis (execute com o monitor parado). Não inclua o canal de destino nos chats de origem. Fotos dos grupos só são reutilizadas conforme `TG_MEDIA_CHATS`; quando disponível, o publicador usa a imagem oficial da API.

O publicador utiliza `SHOPEE_APP_ID`, `SHOPEE_SECRET`, `SHOPEE_SUB_ID`, `TELEGRAM_TOKEN` e `TELEGRAM_CANAL` já configurados. No modo integrado, as ofertas de grupos não aguardam intervalo programado. O radar respeita 600 segundos entre tentativas de publicação. O antigo `INTERVALO_PUBLICACOES` não controla mais esse publicador.

## Fluxo

- O monitor captura mensagens novas dos grupos configurados, identifica um único produto Shopee e grava sua URL canônica, preço e cupom. Outras lojas são ignoradas. Não reprocessa o histórico dos grupos.
- O radar mantém os 22 temas e filtros atuais; atualiza `fila_shopee_api.jsonl` a cada rodada com até três candidatos, sem enviar ao Telegram.
- O publicador lê as duas filas, dá prioridade absoluta às ofertas dos grupos e usa o radar quando não há oferta captada elegível. Ofertas dos grupos são consideradas das mais recentes para as mais antigas.
- Cada link é gerado pela API com suas credenciais de afiliado; não reutiliza o link de afiliado do grupo. Não conseguir identificar o produto ou gerar seu link impede o envio.
- As duas fontes compartilham o histórico por loja e produto, inclusive depois de reiniciar ou mudar o dia. O relógio de dez minutos é registrado somente antes de cada tentativa do radar. Envios de grupos não reiniciam esse relógio nem esperam sua liberação.
- O radar revalida preço e critérios da API. As ofertas dos grupos preservam preço/cupom captados; não passam pelos mesmos filtros de nota/vendas/desconto do radar. Esse preço não é verificado no checkout, conforme a decisão de manter o comportamento atual.
- Reservas de envio incerto ficam bloqueadas para evitar repetição; confira o canal se ocorrer timeout.

As travas locais impedem duas instâncias novas do mesmo componente na mesma pasta. Uma versão antiga já aberta não conhece essas travas: feche todas as janelas antigas antes de iniciar. Não execute outra cópia em pasta diferente. O modo integrado usa `--enfileirar`, e não `--publicar`, no radar.

## Verificação

O monitor mostra `Monitorando ... chats` e `Oferta captada`. O radar mostra `Fila do radar atualizada`. O publicador mostra `Publicado ... origem: telegram` ou `origem: radar`. As filas são verificadas aproximadamente a cada segundo. Gerar o link e enviar depende das APIs: imediato significa sem espera programada de minutos. Uma oferta de grupo recebida durante uma requisição em andamento é processada quando ela termina. O radar pode esperar mais de dez minutos quando há ofertas de grupos pendentes. Se o Telegram rejeitar um envio e solicitar pausa, ela se aplica a ambas as fontes.

Testes locais: `py -m unittest discover -p "test_*.py" -v`. Os testes usam dados simulados; login, acesso real aos grupos e APIs precisam ser conferidos no computador de execução.

