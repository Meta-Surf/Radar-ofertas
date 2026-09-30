> O comportamento de segurança descrito continua válido. Os exemplos e passos de pacote são históricos; para operação atual use [docs/operacao.md](docs/operacao.md).

# Links de produtos via desconto.games

O monitor agora aceita desconto.games para produtos, além do suporte anterior
para cupons. Segue redirecionamentos HTTP apenas entre domínios reconhecidos,
identifica o produto e entrega a URL normalizada à API Shopee. O link do terceiro
não é publicado como se fosse do afiliado do usuário. godg.me continua excluído.

Na oferta LG UltraWide, preserva R$ 1.661,39 no PIX e a instrução de ativar
5% OFF no carrinho, sem subtrair novamente esse desconto.

## Limitação confirmada neste exemplo

Em 27/09/2026, a consulta HTTP a https://desconto.games/5MqFmad retornou 403:
`This link can only be opened in a verified browser.` Não foi possível descobrir
o destino real nesse ambiente. Não existe garantia de conversão automática para
links que exigem navegador verificado, login ou JavaScript. O sistema não
contorna essa exigência nem procura um produto parecido por título.

## Converter com sua API, sem publicar

Na pasta do bot, com as credenciais Shopee já configuradas no .env:

    py converter_link.py "https://desconto.games/5MqFmad"

Se o servidor permitir o redirecionamento HTTP, o programa imprime seu novo link
de afiliado. Para diagnosticar sem acessar a API:

    py converter_link.py "https://desconto.games/5MqFmad" --somente-diagnosticar

Se houver a exigência de navegador:

1. Abra a oferta no seu navegador normalmente e confirme que chegou ao monitor LG.
2. Copie o endereço direto do produto na Shopee (com IDs da loja e do produto).
3. Execute, substituindo URL_DIRETA pelo endereço real:

    py converter_link.py "https://desconto.games/5MqFmad" --destino "URL_DIRETA"

Esse comando salva sua associação em destinos_confirmados.json e gera o link
pela sua API. O cadastro vale somente para a origem exata informada, e será
lido nas próximas capturas. Ele não comprova por si só que o destino corresponde
à oferta: confirme o produto ao copiar. Se o encurtador mudar de destino depois,
atualize ou remova essa entrada. Não cadastre resultados de busca aproximados.

Nenhum comando envia publicação ao Telegram. Ofertas já ignoradas não são
reprocessadas automaticamente. Não publique sem conferir validade e condições.

## Instalação incremental

Este pacote inclui também as duas correções anteriores do monitor (parcelamento,
separação de cupons, revisões repetidas e diagnósticos). A base necessária é a
atualização cumulativa de marcas/fila/histórico de 27/09.

Faça backup; feche monitor e publicador quando puder instalar; extraia o pacote
na pasta do bot, preservando .env, sessão, bancos, filas e imagens; reinicie
monitor/publicador. Se usar INICIAR_INTEGRADO.bat, feche também o radar antes,
para evitar abrir duas instâncias. A atualização do GitHub não altera o Windows.

119 testes locais aprovados com rede e API simuladas. Incluem redirecionamentos,
403, domínio desconhecido, ciclos, cadastro por URL exata, preço/condições e
conversão com URL normalizada. Nenhum link real desta oferta foi gerado ainda.
