> **DOCUMENTO HISTÓRICO.** Registra uma correção incremental de 28/09/2026. Para operação atual, use [docs/operacao.md](docs/operacao.md) e [INTEGRACAO.md](INTEGRACAO.md).

# Correção de classificação de cupom — 28/09/2026

O log da oferta AOC mostra dois bloqueios independentes:

1. A página de cupom /m/espaco-tecnologia foi enviada ao resolvedor de produtos.
   A regra não reconhecia “cupom” no singular e perdia o contexto diante de 👉.
2. O endereço desconto.games/fhxh34L retornou HTTP 403. Sem o destino exato,
   o monitor não pode gerar o link de afiliado do produto nem publicar a oferta.

A correção resolve o primeiro problema: aceita cupom/cupons, ignora marcadores
antes da URL e encerra o bloco ao encontrar “Compre aqui”. O endereço de resgate
vai para o fluxo de cupons; somente desconto.games/fhxh34L vai para o resolvedor
do produto. Preserva R$ 1.103,08 no PIX; não subtrai novamente os R$ 100.

Esta atualização NÃO resolve o HTTP 403 nem comprova o destino real do AOC.
O teste de navegador nesta sessão não concluiu. A solução automática para links
que exigem navegador verificado permanece pendente; não foi instalado um
resolvedor de navegador no computador do usuário. Não há repostagem automática.

## Aplicação

Pacote incremental para a versão com redirecionadores de 27/09/2026.
Faça backup, feche monitor e publicador quando puder, substitua ofertas_core.py
pelo arquivo deste pacote e reinicie esses processos. Preserve .env, sessão,
bancos, filas e imagens. Nenhum processo no Windows foi alterado remotamente.

Testes específicos cobrem a mensagem AOC completa, cupom de R$ 100 separado,
preço no PIX, sete formatos de marcador e encerramento do bloco em “Compre aqui”.

Validação completa: 122 testes locais aprovados, com rede e API simuladas.
