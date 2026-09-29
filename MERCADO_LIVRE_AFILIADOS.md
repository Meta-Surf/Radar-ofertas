# Afiliados Mercado Livre — geração automática

Esta integração reproduz em Python o fluxo usado pelo Link Builder do Mercado Livre.
Ela não depende do OAuth público: usa a sessão autenticada do navegador e o endpoint
interno `/affiliate-program/api/v2/affiliates/createLink`.

## Segurança

Cookie, X-CSRF-Token e tag ficam apenas no `.env` local. Nunca envie esses valores
ao GitHub, Telegram ou documentação. O código não imprime o Cookie nem a resposta
crua da requisição.

## Configuração inicial

1. Entre normalmente em `https://www.mercadolivre.com.br/afiliados/linkbuilder`.
2. Abra F12 > Network/Rede e gere um link manual pelo Link Builder.
3. Selecione a requisição `createLink`.
4. Em Request Headers copie o valor completo de `Cookie` e `X-CSRF-Token`.
5. No Request Payload confira a propriedade `tag`.
6. Grave apenas no seu `.env` local:

```env
ML_AFFILIATE_COOKIE=...
ML_AFFILIATE_CSRF=...
ML_AFFILIATE_TAG=...
ML_AFFILIATE_REFRESH_COOKIES=1
```

Não coloque aspas extras ao redor do Cookie.

## Teste isolado — não publica

```powershell
py mercadolivre_afiliados.py --testar "URL_DIRETA_DO_PRODUTO"
```

Sucesso esperado:

```text
Link de afiliado Mercado Livre gerado:
https://meli.la/...
Nada foi publicado no Telegram.
```

401/403 indica sessão/CSRF recusados. 429 indica limitação temporária. Em todos esses
casos a publicação automática fica bloqueada; não existe fallback para link comum ou
link de afiliado de terceiros.

## Fluxos

### Grupo manual próprio

O grupo definido em `ML_MANUAL_CHAT` continua preservando exatamente o link que o
operador publicou. A nova integração não reconverte esse caminho.

### Grupos de terceiros

O monitor resolve o link Mercado Livre, remove a identidade do link original,
registra o produto canônico como `ml_offer`, preserva o preço explícito da mensagem
e pode completar título/imagem a partir da página pública. O publicador então gera
um novo `short_url` com a sessão configurada e só depois envia ao canal.

Fluxo:

`grupo de terceiro -> produto ML canônico -> createLink -> seu short_url -> Telegram`

## Sessão

Antes de gerar o link, o módulo visita o Link Builder com o Cookie atual e mescla
cookies renovados devolvidos pelo servidor. Isso reduz renovações manuais, mas não
elimina a expiração da sessão principal. Quando houver 401/403, atualize Cookie e
X-CSRF-Token pelo navegador.

## Limites

O endpoint é interno e pode mudar sem aviso. Por isso toda a integração está isolada
em `mercadolivre_afiliados.py`. A geração só é considerada válida quando a resposta
contém exatamente um `urls[0].short_url` HTTPS em domínio Mercado Livre/meli.la.

A leitura pública de título/imagem pode receber 403 ou páginas ambíguas. Nessa
situação a oferta fica pendente; não se inventam dados e não se publica sem cumprir
as travas atuais de preço/imagem.
