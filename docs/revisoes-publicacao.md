# Revisão selecionada e autorização de envio

O publicador obtém, junto da candidata, a fila, chave e SHA-256 do payload
exato persistido. Esses dados não são recalculados a partir da oferta enriquecida
ou do horário em que um callback terminou. A ordenação nativa da captura
(edit_date/date, integrantes e digest) continua sendo responsabilidade da fila.

Ledger.reserve compara a revisão e grava sua ligação ao product/day na tabela
aditiva publication_selections, na mesma transação da reserva. Uma candidata
sem identidade persistente não pode ser enviada pelo publicador automático.

**A fronteira de autorização é o commit de reserved para sending.** Dentro de
BEGIN IMMEDIATE, mark_sending confere ligação da reserva, chave, digest e
expiração atuais antes da transição. Se a revisão mudou, apenas a reserva
correspondente ainda reserved pode ser cancelada. SENT, SENDING e UNCERTAIN
não são liberados. Uma consulta antes de reserve, sozinha, não autoriza envio.

Para produtos KaBuM, o Gate registra também o digest da linha completa do
catálogo que leu. Na fronteira de autorização, uma segunda conexão obtém
BEGIN IMMEDIATE no catálogo **antes** da transação do ledger. Mantém seu lock
enquanto compara esse digest e até o commit de sending no ledger. Assim,
uma atualização concluída no catálogo antes de enqueue impede o envio antigo.
Um writer que espera esse lock só conclui depois da autorização. O coletor
grava/libera catálogo antes de enqueue; nunca deve adquirir os locks em ordem
inversa. A autorização escreve somente no ledger e encerra a transação de
leitura do catálogo com rollback. Não pressupõe commit atômico entre bancos.
Não há API, navegador ou Telegram enquanto esses locks são mantidos.

Esse protocolo utiliza a exclusão de writers do SQLite e é testado com
conexões independentes em DELETE e WAL. Ver
[documentação oficial de transações](https://sqlite.org/lang_transaction.html).

Depois da autorização, uma nova edição permanece válida como candidata nova.
A confirmação registra em posts/deliveries/price_history o conteúdo efetivamente
enviado. Sua ligação à seleção autorizada é conferida; o mesmo message_id é
idempotente, outro ID é recusado. Confirmação e descarte condicional da chave/
digest consumidos pertencem à mesma transação do ledger. Outra origem, fila ou
revisão do mesmo produto não é removida. A nova revisão continua sujeita ao
Gate, deduplicação e intervalo existentes; não recebe permissão de republicar
apenas por ter sobrevivido à confirmação antiga.

Retries novos usam identidade da fila/origem e revisão. A escrita de uma falha
confere a revisão dentro de BEGIN IMMEDIATE; clear usa revisão esperada. Retries
legados equivalentes continuam respeitados. Resiliência ML legada e circuitos
globais permanecem ativos; novas falhas de leitura ficam vinculadas à seleção.
Caches de preparação de cupons e leitor ML distinguem revisões nativas.
Métricas de uma tentativa antiga não sobrescrevem a mensagem editada na fila.
O indicador de última publicação não é alterado por esta correção.

Fotos Telegram novas são publicadas localmente por revisão nativa: nome inclui
chat, mensagem e digest completo. Download e rebranding usam staging; o arquivo
completo é ligado ao nome definitivo sem substituir outro. Edição durante envio
não modifica a foto da revisão autorizada. Arquivos legados são preservados.

Crash antes de sending permite recuperar somente reserved. Crash depois da
autorização permanece conservador: startup converte sending em uncertain e
impede reenvio, mesmo que a fila já tenha uma revisão nova. Os testes injetam
crashes antes/depois de autorização, aceitação remota simulada e dentro/depois
da confirmação; não fazem envios externos.
