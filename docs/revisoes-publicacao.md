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

Liberação por falha com não-envio conhecido e transição para UNCERTAIN também
comparam a ligação persistente da reserva em sua transação. Comparam a revisão
autorizada, e não a nova linha da fila: uma edição durante envio não impede
preservar a incerteza da tentativa anterior. Callbacks de outra seleção não
liberam nem modificam a reserva atual; SENT/UNCERTAIN não são liberados.

Fotos Telegram novas são publicadas localmente por revisão nativa: nome inclui
chat, mensagem e digest completo. Download e rebranding usam staging; o arquivo
completo é ligado ao nome definitivo sem substituir outro. Edição durante envio
não modifica a foto da revisão autorizada. Arquivos legados são preservados.

Crash antes de sending permite recuperar somente reserved. Crash depois da
autorização permanece conservador: startup converte sending em uncertain e
impede reenvio, mesmo que a fila já tenha uma revisão nova. Os testes injetam
crashes antes/depois de autorização, aceitação remota simulada e dentro/depois
da confirmação; não fazem envios externos.

## Voucher anexado a produto KaBuM

Produtos `kabum_feed/product_offer` com cupom persistem uma unidade
`kabum_voucher`: produto/destino oficial, promotion_id, tipo, código, início,
fim, termos e metadados da mesma promoção. Vouchers diferentes para o mesmo
produto são recusados pelo coletor; duplicatas idênticas representam uma
unidade. Não se combina o código de uma promoção com datas de outra.
Datas nativas exigem offset e são normalizadas em UTC; o período é
`start <= now < end`. SHA-256 desses campos não inclui relógio volátil.

O Gate exige a mesma identidade comercial na Offers API. Reutiliza somente
a lista no cache existente de 60 segundos e recalcula o prazo em toda
avaliação. Promoção removida, metadados ausentes/contraditórios e alteração
comercial aguardam nova revisão com CUPOM_VALIDADE_NAO_CONFIRMADA, sem
descarte definitivo. Falha de API produz VALIDACAO_INDISPONIVEL; início futuro
produz CUPOM_NAO_INICIADO; prazo encerrado produz CUPOM_EXPIRADO e descarte
condicional da revisão exata. O cupom não é removido para liberar o produto.

Na fronteira reserved -> sending, depois de obter ambos os locks, o ledger
confere localmente o voucher persistido contra a prova do Gate e recalcula
o prazo imediatamente antes do UPDATE. Não chama API sob lock. Expiração
cancela somente a reserva RESERVED vinculada à seleção; nunca SENT, SENDING,
UNCERTAIN ou uma reserva de outra seleção. Após autorização, uma edição nova
é preservada e a confirmação continua descrevendo o conteúdo enviado.

O TTL operacional de duas horas permanece: antecipar a expiração da fila
eliminaria o registro antes da classificação explícita pelo Gate e exigiria
alterar o contrato geral de enqueue/pruning. As duas barreiras comerciais
impedem envio mesmo com a linha ainda presente. Produto sem cupom e alertas
genéricos KaBuM mantêm seus caminhos e custos anteriores. Entradas legadas
com código sem proveniência ficam bloqueadas até atualização/expiração.

## Período nativo de produto Shopee

Produtos `source=shopee_api/store=Shopee` conservam `shopee_offer_period`
com shop_id/item_id e início/fim nativos em epoch seconds inteiros. A prova
comercial não contém relógio de consulta. Seus campos participam do payload
persistido e do digest da seleção; não há migração de timestamps artificiais.
O período exige start > 0, end > start e start <= now < end.

O refresh usa exatamente o node do shop/item, recusa matches comerciais
conflitantes e conserva o período reconsultado durante link/details. Gate
normaliza a prova original e a preparada: diferença de período exige nova
revisão persistida (REVISAO_COMERCIAL_ALTERADA); não renova silenciosamente
a revisão antiga. Recalcula o período após toda preparação, antes do preço:
OFERTA_EXPIRADA, OFERTA_NAO_INICIADA ou OFERTA_VALIDADE_NAO_CONFIRMADA são
distinguíveis. Campos ausentes/invalidos e falhas de API aguardam retry;
expiração comprovada descarta somente a seleção correspondente.

Mark_sending recebe uma prova local separada, compara com o payload persistido
sob o lock da seleção/reserva e recalcula epoch depois da espera, imediatamente
antes de reserved -> sending. Não consulta Shopee nessa transação. Só a reserva
RESERVED vinculada à seleção é cancelável; outro day, reserva substituta,
SENT/SENDING/UNCERTAIN e revisão nova continuam protegidos. Uma atualização
posterior à autorização não muda a confirmação do conteúdo realmente enviado.

Itens legados sem período aguardam a próxima coleta completa, mesmo quando
prepare conseguiu uma prova nova. TTL operacional/pruning permanecem, pois
a segurança depende das duas barreiras; não se amplia o contrato geral de fila.
Cupons, capturas Shopee de grupo, ML, KaBuM e shadow mantêm seus caminhos.

O modo direto `radar_shopee_continuo --publicar` também persiste/seleciona a
oferta antes de preparar, executa o mesmo Gate e usa a mesma autorização.
Assim não existe bypass do período pelo modo antigo de envio. Timeout depois
da autorização conserva UNCERTAIN vinculado; confirmação usa a seleção exata.
O serviço de produção permanece em `--enfileirar`, sem envio direto ativado.

## Prazo de alertas capturados ML/Shopee

Para `kind=coupon_alert/source=telegram/store=Mercado Livre|Shopee`, Gate
produz `_coupon_deadline_proof` somente depois da filtragem já existente.
A prova liga a seleção original e seu digest semântico à identidade final,
texto/códigos/condições/URLs/entries aprovados e seus prazos normalizados.
SHA-256 determinístico cobre conteúdo e associações; nenhum relógio do Gate
ou da autorização entra na prova. `unspecified` é explícito e não recebe TTL.

ML compartilha a mesma associação global/individual de validate_deadlines;
Shopee compartilha deadline_status/deadline_metadata. A gramática, timezone
Brasília, data sem hora até meia-noite seguinte e limite exclusivo permanecem.
Não existe terceiro parser temporal na autorização.

O publicador remove a prova privada e passa o mesmo objeto final e a prova
separadamente a mark_sending. Dentro do protocolo da seleção/reserva,
depois do lock SQLite, confere revisão, prova e digest do alerta final. Só
depois dessa conferência lê o relógio e compara os deadlines antes do UPDATE
RESERVED → SENDING. Não reinterpreta a captura bruta, não refiltra entries,
não muda texto/identidade e não faz rede/link/API sob transação.

Qualquer prazo aprovado encerrado bloqueia o conjunto inteiro com
CUPOM_EXPIRADO. Prova ausente/unknown/malformada ou divergente bloqueia com
CUPOM_VALIDADE_NAO_CONFIRMADA. Cancela apenas a reserva própria ainda
RESERVED, vinculada à seleção. Nova revisão, outra origem/day e estados
SENT/SENDING/UNCERTAIN ficam preservados. Uma próxima rodada pode voltar
ao Gate, filtrar a lista restante e gerar sua nova identidade normalmente.

Depois do commit SENDING a tentativa externa prevalece: expiração durante
Telegram não regride para RESERVED. Confirma o conteúdo enviado ou conserva
UNCERTAIN em falha/crash. Provas privadas não chegam ao sender, caption,
delivery confirmada nem source_offer público do shadow. TTL/pruning,
intervalos, indicador e schema não mudam.

## Alerta genérico KaBuM/Awin

Somente `coupon_alert/kabum_awin_coupon/KaBuM` persiste a unidade nativa
`kabum_coupon_unit` no JSON existente: advertiser 17729, promotion_id, tipo,
código, destino oficial, início/fim UTC com offset obrigatório e texto/termos
nativos. O ID base permanece `KaBuMCoupon:<promotion_id>`. Horários de coleta,
score, urgência, tracking e TTL não definem identidade comercial. Condições
nativas são preservadas mesmo quando sua URL não aparece na legenda limpa.
Não há alteração de schema. Entradas legadas sem essa evidência aguardam o
refresh normal do coletor, sem migração presumindo termos não persistidos.

O Gate compara todas as correspondências Awin oficiais. Só colapsa unidades
semanticamente idênticas; ausência, resposta parcial ou conflito gera retry
`CUPOM_VALIDADE_NAO_CONFIRMADA`. Falha de API gera `VALIDACAO_INDISPONIVEL`.
Uma mudança de início/fim, código, destino ou condição gera
`REVISAO_COMERCIAL_ALTERADA`: não renova a seleção antiga em memória. O ciclo
normal de collect_alerts/enqueue grava a nova revisão, distinguida pelo digest
do payload. O cache de lista continua em 60s e o período é recalculado em cada
Gate: início inclusivo, fim exclusivo, sem interpretação textual ou fim do dia.

Depois de confirmar a mesma unidade, `_kabum_coupon_proof` liga seleção,
digest comercial e conteúdo final (incluindo tracking) sem relógios voláteis.
O publicador extrai essa prova privada antes do sender/shadow. Sob o protocolo
existente, mark_sending verifica primeiro binding e digest persistidos. Depois
de qualquer espera por lock e de todas as comparações, confere a prova e lê o
relógio local junto ao UPDATE, sem API ou geração de links na transação.
O TTL `queue_expires_at=end_date` é uma defesa adicional, preservada.

Antes de sending, falha cancela somente product/day reservado com o binding
conferido; não remove a revisão nova nem SENT/SENDING/UNCERTAIN ou outro dia.
Depois do commit sending, expiração durante o envio não desfaz a tentativa:
confirmação, incerteza e recuperação de crash seguem o protocolo anterior,
registrando o conteúdo efetivamente enviado e preservando outra revisão.

## Identidade do destino Amazon

A associação é local: ASIN solicitado, item.asin e ASIN do detailPageURL devem
coincidir após normalização uppercase, usando o parser de produto existente.
_parse_item recusa divergência antes de retornar preço/estoque/imagem/link.
valid_affiliate_url confere HTTPS, host, caminho, porta/userinfo e tag; não
constitui sozinho prova de que o link pertence ao ASIN solicitado. /dp e
/gp/product continuam suportados, sem ampliar os formatos aceitos do link.

O Gate repete a associação affiliate_url/URL canônica/product_id, todos Amazon.
Forma/tag inválidas geram LINK_INVALIDO; ASIN divergente com link válido gera
PRODUTO_INCONSISTENTE. Também exige que a identidade preparada permaneça igual
à da captura, protegendo contra preparador alternativo/cache/mutação. Preço e
disponibilidade continuam com suas validações independentes; a preparação
continua exigindo imagem e demais condições existentes.

Toda comparação ocorre antes de reserve/mark_sending; nenhuma chamada HTTP
foi adicionada. Não há cache de resultado comercial Amazon: apenas token OAuth
do mesmo cliente é reutilizado, com itemIds específicos em cada request_item.
Recusa antiga mantém as proteções de seleção/revisão contra descarte/backoff
de uma captura nova; não gera confirmação nem histórico de preço.

Essa proteção não ativa Amazon. Credenciais/configuração/destinos permanecem
como estavam; validação local usa apenas API e envio simulados. O protocolo
Ledger, crash, UNCERTAIN e idempotência permanece inalterado.

## Disponibilidade Mercado Livre automática

O resultado automático do AutoReader inclui `stock_confirmed`, `stock_status`,
`stock_source=structured_product`, `stock_product_id` e `stock_resolved_url`.
Somente disponibilidade explícita InStock do mesmo produto/variante autoriza
prosseguir. Preço e estoque são obrigações independentes. A ausência da prova
produz VALIDACAO_INDISPONIVEL/retry; não herda um booleano da captura.

`MLCommercialUnavailable` distingue OutOfStock/SoldOut/Discontinued de falhas
de leitura. A recusa é propagada por fetch_http, fetch_browser e enrich, sem
buscar recomendações ou outro fallback. O Gate usa SEM_ESTOQUE_COMPROVADO,
descartando apenas a seleção/revisão atual; uma captura nova pode ser validada.
O publicador também trata essa recusa no caminho de preparação ml_offer_pending
antes do Gate, evitando cadastrá-la como falha transitória/quarentena.
`MLProductInconsistent` preserva a distinção de produto/variante divergente.
HTTP403/404/429/500, timeout, captcha, HTML/schema parcial e falha do navegador
continuam sendo indisponibilidade de validação, não prova de produto removido.

No Perfil Social, preço/imagem/CTA não provam estoque. Avisos negativos no card
são recusados. Para ofertas automáticas, o navegador faz uma única navegação
adicional ao CTA oficial exato, limitado a 20s de navegação e 5s de espera por
schema. Exige InStock estruturado do mesmo produto e parâmetros de variante
explicitamente presentes; não infere variante. Retorno ao Social/ciclo,
identidade divergente ou ausência de prova não autorizam publicação. Essa
confirmação não inicia novo fetch_browser/fallback recursivo. A confirmação já
lê o preço estruturado desse mesmo item, usado pelo Gate automático para evitar
conservar preço do card contradito pela leitura oficial.
Não calcula descontos ou preço de parcelas; a regra de preço maior/igual/menor
continua a existente. O caminho manual conserva sua leitura do card.

Manual recente e texto especial mirror mantêm suas políticas deliberadas.
Leitura manual Social não ganha a obrigação automática de confirmação positiva;
recusas explícitas, quando obtidas pelo leitor, permanecem negativas. O Gate
não usa prova de uma identidade para liberar outra.

Cache: resultado positivo com prova e recusa comercial tipada usam a chave
existente de produto/URL/original_url/captura/revisão. TTLs 300s de preparação,
60s no Gate e limite 128 permanecem. Nova revisão tem nova chave; após TTL há
reconsulta. Nenhum relógio entra no fingerprint. Não há schema/pruning novos.

Disponibilidade é uma verificação externa feita antes da reserva/autorização.
Nenhum HTTP/browser foi adicionado ao Ledger ou a BEGIN IMMEDIATE. Estoque
pode mudar entre essa leitura (ou seu cache curto) e o envio; o sistema não
promete disponibilidade contínua durante a chamada Telegram. O protocolo de
revisão, confirmação, UNCERTAIN e crash permanece o já documentado.
