# Amazon Brasil — Creators API

Integração oficial preparada para o Radar de Ofertas.

## Estado atual

O código está implementado em `amazon_afiliados.py` e integrado ao monitor, publicador, Gate pré-publicação e histórico.

A produção permanece **fail-closed** enquanto estas variáveis estiverem vazias:

```env
AMAZON_PARTNER_TAG=
AMAZON_CREATORS_CREDENTIAL_ID=
AMAZON_CREATORS_CREDENTIAL_SECRET=
AMAZON_CREATORS_TIMEOUT=30
```

Sem essas credenciais, ofertas Amazon captadas ficam aguardando e não são publicadas.

## API usada

A integração usa a Amazon Creators API atual, com OAuth 2.0 e marketplace `www.amazon.com.br`.

O fluxo implementado:

1. identifica o ASIN a partir de um link Amazon Brasil;
2. obtém token OAuth no endpoint regional da América do Norte;
3. consulta `catalog/v1/getItems`;
4. solicita título, imagem e `OffersV2`;
5. exige preço em BRL;
6. exige disponibilidade `IN_STOCK`;
7. aceita apenas produto novo;
8. usa o `detailPageURL` devolvido pela API como link afiliado;
9. valida que o `tag` do link é o `AMAZON_PARTNER_TAG` configurado;
10. passa pelo Gate antes de publicar.
## Regra de preço

O Radar não raspa preço da página Amazon e não estima desconto.

O preço publicado é o valor da oferta destacada devolvido por `OffersV2.Listings.Price.Money`.

Se a Creators API não retornar preço exato em BRL, a oferta é bloqueada.

Se o preço captado no grupo for maior que o preço atual da API, a publicação usa o menor valor atual comprovado.

Se o preço captado for menor que o preço atual da API, o Gate bloqueia a oferta em vez de publicar um preço que já aumentou.

## Disponibilidade

A Creators API precisa retornar `IN_STOCK`.

Ausência de oferta destacada, produto sem estoque ou condição diferente de novo bloqueia o envio.

## Link de afiliado

Não é construído um link manual como fallback.

O Radar usa somente o `detailPageURL` retornado pela própria Creators API e verifica o Partner Tag configurado.

## Teste isolado

Depois de cadastrar as credenciais no `.env`:

```bash
cd /opt/radar
./.venv/bin/python amazon_afiliados.py --testar "https://www.amazon.com.br/dp/ASIN_AQUI"
```

O comando consulta a API e mostra produto, preço, estoque e validade do link. Ele não publica no Telegram.

## Pendência operacional

É necessário cadastrar uma conta Amazon Associados Brasil com acesso à Creators API e inserir as três credenciais no `.env` da VPS.

Nenhuma credencial Amazon deve ser enviada ao GitHub.
