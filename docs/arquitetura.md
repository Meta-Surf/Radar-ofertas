# Arquitetura

## Fluxo Shopee e Telegram

1. `monitor_ofertas.py` recebe mensagens novas dos chats configurados via Telethon e extrai candidatos.
2. `ofertas_core.py` centraliza identificação de produtos, dados das ofertas e registro diário de publicação.
3. A fila local `ofertas_para_revisar.jsonl` conecta o monitor ao publicador.
4. `bot_ofertas_revisao.py` filtra candidatos e chama `shopee_afiliados.py` para gerar o link afiliado e consultar dados do produto.
5. O publicador envia a oferta ao canal pela API do bot. O SQLite registra reservas/publicações para evitar repetição diária.

Falhas na geração do link impedem a publicação. Uma resposta incerta do Telegram preserva a reserva para evitar reenvios. O dia é calculado em `America/Sao_Paulo`.

## Componentes experimentais

`mercadolivre_auth.py` auxilia no OAuth local. `radar_mercadolivre_v6.py` consulta anúncios configurados e pode usar links afiliados previamente mapeados. Essa parte é independente do publicador Shopee; não oferece geração automática de links afiliados Mercado Livre. Alguns acessos a itens ainda retornam HTTP 403.

Amazon, Instagram e WhatsApp permanecem pendentes.

## Dados e execução

Execute os comandos na raiz do projeto. Credenciais, sessão, filas, imagens coletadas e banco permanecem no computador de execução e estão excluídos do Git. O GitHub armazena código e documentação; enviar o projeto não inicia os bots nem mantém o computador ligado.

A automação do GitHub executa somente testes com serviços simulados. Não necessita de secrets de produção.
