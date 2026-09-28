# Entrada manual Mercado Livre — grupo -1003988174916

ATUALIZAÇÃO: agora mensagens somente com link podem ser completadas automaticamente.
Instale instalar_ml_automatico.cmd e siga MERCADO_LIVRE_AUTOMATICO.md. O formato
com dados explícitos abaixo continua aceito como alternativa.

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
Mercado Livre. A lista sai inteira, com a arte quando couber na legenda. Todos os alertas de cupons incluem seu Social diretamente no texto, sem botões;
links da origem são descartados. Veja CUPONS_SOCIAL_NO_TEXTO.md.

Só mensagens novas e edições recebidas enquanto o monitor estiver ativo serão
captadas. Não percorre todo o histórico. Não realiza publicações de teste reais.

## Validação

141 testes locais aprovados com Telegram e APIs simulados. Inclui captura real do
handler com foto e edição, origem restrita, preservação exata do link, preço
obrigatório, múltiplos links, cupons, prioridade dos grupos e deduplicação.
O grupo no Windows só ficará ativo depois que esta atualização for instalada.
