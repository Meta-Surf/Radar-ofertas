# Correção do monitor de grupos — 27/09/2026

## Problemas reproduzidos

Na mensagem do Kit Ventoinha Pichau Ventus NX, o extrator rejeitava o trecho
`R$ 113 em 12x sem juros`. O valor explícito agora é preservado como R$ 113,00,
e `em 12x sem juros` fica na linha de condições. Não se calcula parcela ou total
por multiplicação. Expressões como `12x de R$ 113` continuam sem preço total.

A linha `R$ 10 OFF` entre o rótulo de resgate e o link fazia perder o contexto
da página de cupons. O monitor agora mantém as condições até esse link e encerra
o contexto antes do link do produto. O desconto não é subtraído do preço.
`Cupom: não identificado` continua correto quando não há código explícito.

Atualizações com texto, links e mídia idênticos são ignoradas por até uma hora
em memória (limite de 2.048 mensagens). Mudanças de preço, link ou mídia continuam
sendo capturadas; mensagens diferentes e grupos diferentes não são confundidos.
Isso reduz capturas/logs repetidos, sem substituir a deduplicação persistente de
publicações. Reiniciar o monitor limpa apenas esse cache de revisões.

Falhas de resolução agora mostram o motivo, por exemplo HTTP 403, HTTP 429 ou
timeout. Apenas erros de rede, 429 e 5xx têm até três tentativas (pausas de 1 e 2
segundos). Bloqueios 403 não são contornados. Se algum link candidato a produto
ficar sem resolução, a mensagem permanece bloqueada para não associar preço e
imagem a um produto errado. Não foi comprovada a causa individual dos outros
links mencionados no log; faltam as mensagens e os diagnósticos desses casos.

## Aplicação

Este pacote contém somente a correção incremental, para quem já instalou a
atualização cumulativa de marcas/fila/histórico de 27/09/2026.

1. Faça uma cópia dos arquivos atuais antes da substituição.
2. Quando puder aplicar, feche monitor e publicador com Ctrl+C.
3. Extraia o ZIP na pasta do bot, substituindo `monitor_ofertas.py` e
   `ofertas_core.py`. Preserve as pastas e os demais arquivos.
4. Reinicie monitor e publicador uma vez. Caso use `INICIAR_INTEGRADO.bat`,
   feche também o radar antes, pois esse inicializador abre os três processos.

Não apague nem substitua `.env`, sessão, bancos, filas ou imagens. O pacote não
contém esses dados. Atualizar o GitHub não altera o bot em execução no Windows.
Ofertas já ignoradas não são reenviadas automaticamente; a correção vale para
novas capturas e edições efetivas. Não foi realizada nenhuma publicação real.

## Validação

107 testes locais aprovados com APIs simuladas. A regressão completa exercita
captura, fila, preço de R$ 113, condição em 12x, separação do cupom de R$ 10,
imagem, edição idêntica e edição posterior de preço. Também verifica limites
de repetição, escopo dos links, cache de revisões e diagnóstico de falhas.
