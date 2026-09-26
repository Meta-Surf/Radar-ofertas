# Grupos + radar Shopee

## Instalação

1. Pare as janelas antigas do radar, monitor e publicador com Ctrl+C.
2. Faça backup da pasta e extraia os arquivos deste pacote na mesma pasta do bot.
3. Preserve `.env`, `monitor_ofertas.session`, `publicacoes.sqlite3` e `imagens_ofertas`. Nenhum deles acompanha este pacote.
4. Instale as dependências com `py -m pip install -r requirements.txt`.
5. Abra `INICIAR_INTEGRADO.bat`. Ele abre três janelas, que devem ficar abertas.

Se preferir iniciar manualmente, execute cada comando em uma janela na mesma pasta:

```powershell
py -u monitor_ofertas.py
py -u radar_shopee_continuo.py --enfileirar --loop --intervalo 300 --limite 3
py -u bot_ofertas_revisao.py
```

O último comando publica de verdade no `TELEGRAM_CANAL` do `.env`. Para conferir as filas sem enviar, use `py bot_ofertas_revisao.py --simular` antes de iniciar o publicador real.

## Configuração existente

O monitor usa `TG_API_ID`, `TG_API_HASH` e `TG_CHATS`. Se a sessão ainda não estiver autenticada, siga o pedido de login na janela do monitor. `py monitor_ofertas.py --listar` mostra os chats disponíveis (execute com o monitor parado). Não inclua o canal de destino nos chats de origem. Fotos dos grupos só são reutilizadas conforme `TG_MEDIA_CHATS`; quando disponível, o publicador usa a imagem oficial da API.

O publicador utiliza `SHOPEE_APP_ID`, `SHOPEE_SECRET`, `SHOPEE_SUB_ID`, `TELEGRAM_TOKEN` e `TELEGRAM_CANAL` já configurados. `INTERVALO_PUBLICACOES` tem mínimo de 300 segundos, mesmo se o `.env` antigo contiver 30.

## Fluxo

- O monitor captura mensagens novas dos grupos configurados, identifica um único produto Shopee e grava sua URL canônica, preço e cupom. Outras lojas são ignoradas. Não reprocessa o histórico dos grupos.
- O radar mantém os 22 temas e filtros atuais; atualiza `fila_shopee_api.jsonl` a cada rodada com até três candidatos, sem enviar ao Telegram.
- O publicador lê as duas filas, alterna prioridade entre grupos e radar e usa a outra fonte quando a preferida não tem oferta elegível. Ofertas dos grupos são consideradas das mais recentes para as mais antigas.
- Cada link é gerado pela API com suas credenciais de afiliado; não reutiliza o link de afiliado do grupo. Não conseguir identificar o produto ou gerar seu link impede o envio.
- As duas fontes compartilham o histórico por loja e produto, inclusive depois de reiniciar ou mudar o dia. A pausa de cinco minutos também é registrada antes de cada tentativa de envio.
- O radar revalida preço e critérios da API. As ofertas dos grupos preservam preço/cupom captados; não passam pelos mesmos filtros de nota/vendas/desconto do radar. Esse preço não é verificado no checkout, conforme a decisão de manter o comportamento atual.
- Reservas de envio incerto ficam bloqueadas para evitar repetição; confira o canal se ocorrer timeout.

As travas locais impedem duas instâncias novas do mesmo componente na mesma pasta. Uma versão antiga já aberta não conhece essas travas: feche todas as janelas antigas antes de iniciar. Não execute outra cópia em pasta diferente. O modo integrado usa `--enfileirar`, e não `--publicar`, no radar.

## Verificação

O monitor mostra `Monitorando ... chats` e `Oferta captada`. O radar mostra `Fila do radar atualizada`. O publicador mostra `Publicado ... origem: telegram` ou `origem: radar`. Ausência de ofertas elegíveis pode produzir intervalos maiores que cinco minutos.

Testes locais: `py -m unittest discover -p "test_*.py" -v`. Os testes usam dados simulados; login, acesso real aos grupos e APIs precisam ser conferidos no computador de execução.
