> Recurso ainda válido. Os passos de pacote abaixo registram a entrega de 28/09/2026; para operação atual use [docs/operacao.md](docs/operacao.md).

# Social no texto dos cupons — 28/09/2026

Os alertas de cupons Mercado Livre terminam com:

Resgate aqui:
https://www.mercadolivre.com.br/social/bebidastaio

Não há botão. O endereço aparece mesmo quando a mensagem original não contém link.
Links de terceiros e rótulos isolados como “LINK:”/“resgate aqui” são removidos.
Vale também para o grupo de entrada manual; links antigos na fila não viram botões.
Códigos e condições permanecem juntos, respeitando os limites de legenda/texto.

## Aplicar

Faça backup da pasta BOPT. Feche monitor e publicador, extraia o pacote na mesma
pasta substituindo cupons_mercadolivre.py e reinicie seus comandos habituais.
Não altere nem substitua seu .env. Não é necessário reinstalar dependências.
Esta é uma atualização incremental para a versão atual do projeto.

O endereço já está definido. Se desejar mudar depois, acrescente ao .env:
ML_CUPONS_SOCIAL_URL=https://www.mercadolivre.com.br/social/bebidastaio

O botão VER OFERTA de produtos não faz parte desta correção. Nenhuma mensagem de
teste foi enviada; publicações antigas no Telegram não foram editadas.
