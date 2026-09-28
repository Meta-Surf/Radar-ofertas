# Entrada manual Mercado Livre — grupo -1003988174916

## Instalar

Pacote para a versão do Radar de Ofertas com as correções de 28/09/2026. Inclui
os módulos e a arte da atualização anterior de listas de cupons ML.

1. Faça backup da pasta BOPT.
2. Feche monitor e publicador quando puder.
3. Extraia o ZIP na pasta BOPT, substituindo o código e mantendo as subpastas.
4. Preserve .env, sessões, banco, filas e imagens; não estão no pacote.
5. Reinicie os comandos habituais do monitor e publicador.

O ID já está definido no código; não é necessário substituir seu .env nem apagar
os grupos em TG_CHATS. Opcionalmente acrescente `ML_MANUAL_CHAT=-1003988174916` ao
.env para tornar a configuração explícita. Valor vazio desativa essa entrada.
A conta usada pelo monitor deve participar do grupo. TELEGRAM_CANAL continua
apontando para o grupo/canal de ofertas final, não para a entrada manual.

## Como usar

Envie no grupo Publicador Mercado Livre uma imagem (ou link com prévia de foto)
e o texto do produto, por exemplo:

Monitor AOC 27 polegadas
Por: R$ 1.103,08 no PIX
https://meli.la/SEU_LINK_REAL

Substitua o endereço pelo link gerado na SUA conta de afiliado. O bot mantém esse
endereço exato no botão VER OFERTA; não verifica comissão nem gera outro link.
Preço e condições vêm da mensagem. Não estima preço nem consulta API Mercado Livre.
Se o compartilhamento contiver apenas o link, acrescente título e preço e envie
foto se não houver prévia. Se faltar preço, edite a mensagem; o monitor captura
a edição. EXIGIR_IMAGEM=1 continua impedindo ofertas sem foto.

Para cupons, compartilhe a lista com os códigos, condições e, se desejar, seu link
Mercado Livre. A lista sai inteira, com a arte quando couber na legenda. Links
manuais da loja são preservados em botões; links externos são descartados. Nos
outros grupos monitorados os links de terceiros continuam sendo removidos.

Só mensagens novas e edições recebidas enquanto o monitor estiver ativo serão
captadas. Não percorre todo o histórico. Não realiza publicações de teste reais.

## Validação

141 testes locais aprovados com Telegram e APIs simulados. Inclui captura real do
handler com foto e edição, origem restrita, preservação exata do link, preço
obrigatório, múltiplos links, cupons, prioridade dos grupos e deduplicação.
O grupo no Windows só ficará ativo depois que esta atualização for instalada.
