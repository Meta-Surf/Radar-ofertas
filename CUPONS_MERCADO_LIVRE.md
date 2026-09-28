# Cupons Mercado Livre — 28/09/2026

Arte original Radar de Ofertas e captura de listas completas com códigos,
percentuais, compra mínima e limite. Formatação semelhante às mensagens de origem;
links de compras, grupos e afiliados externos são removidos. Não reutiliza os
botões nem as fotos dos grupos. Não afirma que os cupons estão válidos.

Uma lista = uma publicação. Até 1024 caracteres visíveis, foto com legenda;
listas maiores, texto único até 4096. Se ultrapassar 4096, o bot informa o bloqueio:
a lista permanece na fila sem ser repartida ou cortada. Vale a idade máxima normal.

## Aplicar no Windows

Este pacote incremental requer a versão do Radar de Ofertas com as correções
anteriores de 28/09/2026 (main 426bba6 ou posterior antes desta atualização).

1. Faça backup da pasta do bot.
2. Feche o monitor e o publicador quando puder.
3. Extraia este pacote dentro da pasta BOPT, substituindo os arquivos de código
   e mantendo a estrutura assets, docs e tests.
4. Preserve seu .env, sessões, banco e filas. O pacote não contém esses dados.
5. Reinicie os mesmos comandos do monitor e publicador que já utiliza.

Nenhuma mudança de .env é necessária. Cupons ML não dependem da API Shopee.
A arte está em assets/banner_cupons_ml.png. Também pode ser enviada manualmente.
As listas novas dos grupos em TG_CHATS serão captadas. Publicações antigas não são
reprocessadas automaticamente. As duas mensagens de exemplo são usadas em testes,
não adicionadas à sua fila real.

Validação local: testes automatizados de listas, links, limites de legenda,
deduplicação, ausência de credenciais Shopee, rejeições e respostas incertas.
Nenhuma mensagem de teste foi enviada ao Telegram. Esta atualização não resolve
o bloqueio HTTP 403 de desconto.games; esse problema anterior permanece separado.
