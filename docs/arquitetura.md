# Arquitetura

## Fluxo Shopee e Telegram

1. `monitor_ofertas.py` recebe mensagens novas dos chats configurados via Telethon e extrai candidatos.
2. `ofertas_core.py` centraliza identificação de produtos, dados das ofertas e registro diário de publicação.
3. A fila local `fila_ofertas_v2.jsonl` conecta o monitor ao publicador.
4. `bot_ofertas_revisao.py` filtra candidatos e chama `shopee_afiliados.py` para gerar o link afiliado e consultar dados do produto.
5. O publicador envia a oferta ao canal pela API do bot. O SQLite registra reservas/publicações para evitar repetição diária.

Falhas na geração do link impedem a publicação. Uma resposta incerta do Telegram preserva a reserva para evitar reenvios. O dia é calculado em `America/Sao_Paulo`.

## Componentes experimentais

`mercadolivre_auth.py` auxilia no OAuth local. `radar_mercadolivre_v6.py` consulta anúncios configurados e pode usar links afiliados previamente mapeados. Decisão da Fase 0: opção (b), somente monitoramento manual. Todos os modos exibem ofertas no terminal, sem gravar publicações; `enviar_telegram` também bloqueia chamadas diretas. Um permalink comum pode aparecer na prévia, mas nunca é publicado. Um link mapeado não comprova comissão. Essa parte é independente do publicador Shopee; não oferece geração automática de links afiliados Mercado Livre. Alguns acessos a itens ainda retornam HTTP 403.

Amazon, Instagram e WhatsApp permanecem pendentes.

## Dados e execução

Execute os comandos na raiz do projeto. Credenciais, sessão, filas, imagens coletadas e banco permanecem no computador de execução e estão excluídos do Git. O GitHub armazena código e documentação; enviar o projeto não inicia os bots nem mantém o computador ligado.

A automação do GitHub executa somente testes com serviços simulados. Não necessita de secrets de produção.

## Descoberta direta Shopee

`radar_shopee.py` consulta produtos e substitui atomicamente `fila_shopee_api.jsonl`. O publicador lê ambas as filas e usa o mesmo registro diário. Ofertas da API são revalidadas antes da geração do link. Consulte [o guia do radar](radar-shopee.md).
