> Atualização de 27/09/2026: o fluxo atual usa **INICIAR_INTEGRADO.bat**, com grupos/cupons prioritários, radar a cada 600 segundos, 24 temas, marcas preferenciais e fila SQLite de duas horas. Preços divulgados passam a ser registrados após envio confirmado. Os selos de 30/60/90/180 dias exigem variante confirmada, ainda não fornecida pelas fontes atuais. Veja [ATUALIZACAO_RADAR.md](../ATUALIZACAO_RADAR.md) para instalação e limites. Orientações históricas divergentes abaixo não descrevem o fluxo integrado atual.

# Publicador de afiliados Shopee

## O que mudou

O monitor continua identificando ofertas no Telegram. O publicador usa suas credenciais da API de Afiliados da Shopee para gerar um novo link antes de enviar a oferta. O botão usa exatamente o link retornado por essa requisição autenticada. Se não conseguir gerar um link válido, não publica. A consulta opcional de produto traz nome e imagem da API, quando disponíveis, usando os IDs exatos de loja e produto.

A conta das comissões é a conta à qual o App ID e o Secret pertencem. Um link criado com sucesso não garante comissão: a venda precisa ser elegível, atribuída e validada pela Shopee. Use apenas canais de divulgação aceitos e cadastrados no seu programa de afiliados. Confira os resultados no painel da Shopee.

Amazon e Mercado Livre continuam na captura, mas o publicador os deixa pendentes até que suas respectivas integrações de afiliados estejam prontas. Nenhuma oferta dessas lojas é enviada com link comum.

## Instalação no Windows

1. Pare os dois programas com Ctrl+C. Faça backup dos scripts antigos e extraia TODOS os arquivos deste pacote na mesma pasta Telegram, substituindo os scripts. Não rode versões antigas do publicador em paralelo.
2. Preserve `.env`, `monitor_ofertas.session`, o banco e as filas locais. O pacote não contém nem substitui esses dados. Apenas `.env.exemplo` é fornecido como referência.
3. Acrescente ao `.env`:

```text
SHOPEE_APP_ID=COLE_SEU_APP_ID
SHOPEE_SECRET="COLE_SEU_SECRET_DA_API"
SHOPEE_SUB_ID=telegram
```

O Secret (às vezes apresentado como senha da API) NÃO é a senha usada para entrar na conta Shopee. Não compartilhe essas credenciais, seu token do bot ou a sessão do Telegram. Mantenha no .env os campos TG_API_ID, TG_API_HASH, TG_CHATS, TELEGRAM_TOKEN, TELEGRAM_CANAL e as configurações existentes de imagens.

4. Instale as dependências no PowerShell: `py -m pip install -r requirements.txt`.
5. Teste a geração do link sem publicar:

```powershell
py shopee_afiliados.py --testar "https://shopee.com.br/product/627750190/22899341907"
```

Pode substituir pelo link direto de outro produto Shopee. O comando imprime somente o link gerado e mensagens de status; não envia ao Telegram. Abra o link para conferir o produto. O teste utiliza a API real da sua conta, mas não realiza compras.

6. Para captar novas ofertas: `py monitor_ofertas.py`.
7. Em outra janela, teste a fila: `py bot_ofertas_revisao.py --simular`. Essa opção consulta a API para montar as prévias, mas não publica nem reserva os produtos no registro diário. Fila vazia ou sem ofertas recentes resulta em nenhuma prévia.
8. Para iniciar as publicações automáticas, execute `py bot_ofertas_revisao.py`. As duas janelas devem permanecer abertas. Ctrl+C encerra cada programa. Alterar o .env exige reiniciar o programa correspondente.

## Recuperação automática após reinício

Ao iniciar, o monitor consulta os grupos configurados e reprocessa por padrão as mensagens dos últimos 30 minutos. Isso cobre ofertas publicadas enquanto o computador, o monitor ou o `INICIAR_INTEGRADO.bat` estavam desligados.

- `TG_RECUPERAR_MINUTOS=30` define a janela. Use `0` para desativar.
- `TG_RECUPERAR_MAX_MENSAGENS=500` limita quantas mensagens recentes são consultadas por chat em cada inicialização.
- A janela efetiva nunca ultrapassa `IDADE_MAXIMA_MINUTOS`, porque o publicador não enviaria uma oferta mais antiga que esse limite.
- Álbuns são reconstruídos antes da captura, mantendo legenda, links e foto no mesmo lote.
- Cada lote recebe um `capture_digest`. Se a mesma revisão já estiver na fila, ela não é acrescentada novamente no reinício.
- Edições geram outro digest e podem ser reprocessadas normalmente.
- O arquivo local `monitor_recuperacao.json` registra a posição operacional por chat e é ignorado pelo Git. Ele não contém credenciais.
- Uma falha ao consultar um grupo é isolada: os demais grupos continuam sendo recuperados e depois entram no monitoramento ao vivo.
- O banco `publicacoes.sqlite3` continua sendo a barreira final contra republicação de um produto já enviado.

O `INICIAR_INTEGRADO.bat` não precisa de argumento novo: como ele inicia `monitor_ofertas.py`, a recuperação roda automaticamente antes de aparecer a mensagem normal de monitoramento.

### Ordem de publicação

A recuperação não muda a rotação de busca do radar. A prioridade do publicador passa a ser:

1. ofertas novas captadas ao vivo nos grupos;
2. ofertas recuperadas, da mais recente para a mais antiga;
3. radar Shopee.

As recuperadas não são disparadas em rajada: `INTERVALO_RECUPERADAS=30` cria um intervalo mínimo de 30 segundos entre elas. Durante essa pausa, uma oferta nova do grupo pode sair imediatamente e o radar continua podendo publicar se a janela própria de 10 minutos estiver liberada. `IDADE_MAXIMA_RECUPERADAS_MINUTOS=45` descarta uma recuperada que envelheceu demais enquanto aguardava na fila.

## Fotos, cupons e duplicatas

- Usa imagem da API Shopee quando disponível. Caso contrário, usa a foto capturada do Telegram em canais listados por você em TG_MEDIA_CHATS, com autorização de reutilização. A proteção de conteúdo dos grupos é respeitada.
- EXIGIR_IMAGEM=1 mantém ofertas sem imagem pendentes. EXIGIR_IMAGEM=0 permite mensagem sem foto.
- Inclui o nome do produto quando retornado pela API. Preserva o preço e o código de cupom identificados na origem, explicitamente rotulados como informação da origem; não garante validade do cupom ou consulta preço/estoque em tempo real.
- Não copia o link de uma página de cupons que pertence a outro afiliado. Um código de cupom só é incluído se estiver escrito de forma reconhecível na mensagem.
- INTERVALO_PUBLICACOES padrão 30 segundos; IDADE_MAXIMA_MINUTOS padrão 120 minutos desde a mensagem de origem.
- publicacoes.sqlite3 impede repetir o mesmo ID de loja e produto em qualquer data, inclusive entre grupos e depois de reiniciar. Preserve o banco ao atualizar o código; não o apague para forçar republicação.
- Falha ao gerar o link libera a reserva porque nada foi enviado e aplica uma pausa de cinco minutos antes de novas tentativas. Se a resposta do Telegram for incerta (timeout/queda), mantém a reserva para evitar duplicidade mesmo após a virada do dia. Consulte o canal nesses casos.
- Anúncios diferentes de um mesmo produto podem ter IDs distintos; não existe deduplicação visual ou por descrição.
- Não execute monitores simultâneos com a mesma sessão. Não use o banco simultaneamente via OneDrive em computadores diferentes. A fila, o banco e as imagens são locais e ocupam espaço; não há exclusão automática.

## Limitações que permanecem

Links curtos bloqueados com 403 podem continuar sem identificação, mesmo com as credenciais Shopee configuradas. A API de Afiliados não resolve bloqueios do Mercado Livre. A correspondência manual meli.la/2PnnX9t -> MLB4360643061 continua preservada. Conteúdo com vários produtos é ignorado para evitar associações incorretas. O diagnóstico não reprocessa mensagens antigas para publicação.

Para diagnosticar, pare o monitor antes de executar:

`py monitor_ofertas.py --diagnosticar https://t.me/sohardwaredorocha/18776`

O teste de geração de links deve receber um produto Shopee. Se ocorrer falha de autenticação, confira credenciais do painel de API de Afiliados e data/hora automática do Windows. Falhas são exibidas sem o conteúdo de cabeçalhos ou respostas que possam revelar segredos. A configuração não permite trocar o endpoint que recebe as credenciais.

## Validação técnica e fontes

Os testes locais com chamadas externas simuladas, cobrindo reconhecimento de links, duplicatas, assinatura dos bytes enviados, erro de autenticação, ausência de credenciais, rejeição de link inválido, botão com link afiliado e ausência de envio ao Telegram quando a API falha. Não houve uso de suas credenciais nem publicação real durante o desenvolvimento.

Execute `py -m unittest discover -v` para repetir os testes. Eles não usam suas credenciais reais.

A autenticação e o esquema generateShortLink(input: ShortLinkInput), originUrl, subIds e shortLink foram conferidos no HTML e no esquema incorporado dos exploradores oficiais em 25/09/2026:
- https://open-api.affiliate.shopee.com.br/explorer
- https://open-api.affiliate.shopee.com.br/explorer/v2


## Banner incluído no GitHub

A arte original está em `assets/banner_cupons.parts/`, dividida em partes Base64
para evitar a falha do envio binário pela integração. O bot restaura automaticamente
`assets/banner_cupons.png` quando necessário, verificando tamanho e SHA-256.
Não há alteração de pixels ou dependência de download. Para restaurar manualmente:
`py banner_asset.py`. Preserve a pasta de partes ao copiar o projeto.

Incluídas as correções de `**R$ 2.713,08**` e `💵2,943`, captura de edições,
bloqueio de ofertas sem preço, valor em negrito e condições em linha separada.

## Listas de cupons Mercado Livre

O monitor reconhece mensagens com “Mercado Livre” e linhas de desconto terminadas
em código após dois-pontos, como `10% OFF acima de R$ 149, limite R$ 200: HOJETEMPROMO`.
Captura uma lista por mensagem/álbum e preserva seu texto, inclusive selecionados,
valores mínimos, limites e outras condições. URLs, convites e botões da origem não
são publicados. Os códigos ficam em formato copiável. Não verifica validade dos
cupons na loja nem gera links de afiliado ML; as ofertas de produtos ML continuam
aguardando integração própria.

Não há divisão por número de cupons. O envio usa uma única foto com legenda até
1024 unidades UTF-16 de texto visível; acima disso usa uma única mensagem de texto
até 4096. Acima de 4096 registra o bloqueio e mantém a lista na fila, sem cortar
condições. O limite de idade da fila continua valendo. Não envia a arte separadamente.
Listas idênticas com mesma data local são deduplicadas independentemente do link
removido e da ordem das linhas. Textos com condições ou redações diferentes podem
ser tratados como novas listas.

Arte: `assets/banner_cupons_ml.png`, restaurada automaticamente das partes
versionadas quando necessário. Nenhuma nova variável no .env. Credenciais Shopee
não são necessárias para as listas ML. TELEGRAM_TOKEN, TELEGRAM_CANAL e a
configuração existente do monitor continuam necessários. `--diagnosticar` informa
quantos códigos foram reconhecidos sem publicar. `--simular` mostra as listas sem
postar; outros itens Shopee dessa mesma fila ainda podem consultar a API Shopee.

## Publicador Mercado Livre: entrada manual

O grupo `-1003988174916` é a origem manual autorizada. `ML_MANUAL_CHAT` pode
substituir esse ID; valor vazio desativa. O monitor inclui o grupo automaticamente
quando a conta Telegram conectada já participa dele, sem apagar TG_CHATS. Também
permite baixar as fotos desse grupo, inclusive fotos da prévia do link. Se a conta
não o encontrar, avisa e mantém os demais grupos; não entra em grupos por conta
própria. O canal de destino continua sendo TELEGRAM_CANAL e não pode ser a origem.

Envie um link HTTPS do Mercado Livre ou meli.la. A leitura automática descrita
abaixo completa título, preço e imagem ausentes. Também aceita dados já escritos pelo operador. Links repetidos idênticos contam como
um. Dois links de produto diferentes na mesma oferta são recusados, pois não se
sabe a qual produto o preço/imagem pertence. Dados ausentes acionam a consulta automática; sem resultado válido,
a publicação fica pendente e respeita EXIGIR_IMAGEM. Não consulta a API ML. A leitura pública pode expandir o encurtador para consultar
o produto, mas não altera o link do botão nem comprova titularidade de afiliado. Apenas compartilhar um link comum
não o transforma em afiliado.

As ofertas manuais usam título, loja, preço em negrito, condição em linha abaixo,
cupom e botão VER OFERTA. Têm prioridade dos grupos e não esperam os 10 minutos do
radar. A mesma URL tem uma identidade estável para evitar reenvio; URLs diferentes
para o mesmo produto podem não ser reconhecidas como duplicatas. A última edição
pendente substitui a anterior. Oferta já publicada não é reenviada só por edição.

Listas de cupons preservam seus códigos e condições em uma só publicação. Todos
os alertas ML, inclusive da entrada manual, usam o Social configurado diretamente
no texto, sem botões. Links e chamadas de resgate de terceiros são removidos.
O cabeçalho “NOVOS CUPONS” também é aceito no grupo manual, mesmo sem o nome da loja.
Mantêm-se os limites de 1024/4096, regras de idade, conteúdo protegido e resultado
de envio incerto. Diagnóstico e simulação não publicam.

## Leitura automática Mercado Livre

Instale dependências e Chromium com instalar_ml_automatico.cmd. Uma mensagem
somente com link no grupo manual pode ser enfileirada. Antes de validar preço,
o publicador agenda a leitura por HTTP/navegador em segundo plano e completa os
dados ausentes. Os outros itens continuam sendo processados. As tarefas são
limitadas a uma execução e até quatro consultas pendentes, com cache por mensagem
e edição de cinco minutos. Jobs antigos concluídos são recolhidos.

Somente Product/Offer inequívocos, moeda BRL, InStock e imagem mlstatic são aceitos.
Não extrai preços de parcelas/recomendações/faixas. O preço público é identificado
na condição da mensagem; dados explícitos do operador prevalecem. Login, captcha,
403 persistente ou vitrine ambígua não são contornados. CLI mercadolivre_auto.py
--testar URL diagnostica sem publicar. Consulte MERCADO_LIVRE_AUTOMATICO.md para
instalação e limites do teste real do link 2wpg8CQ.

## Social no texto dos cupons Mercado Livre

Cada alerta termina com “Resgate aqui:” e a URL
https://www.mercadolivre.com.br/social/bebidastaio diretamente no texto. Não cria
botão, não reutiliza manual_links de filas antigas e inclui o Social mesmo sem
link na origem. Rótulos isolados como “LINK:” e “resgate aqui” são removidos.
Códigos e condições são preservados. ML_CUPONS_SOCIAL_URL permite configurar
outra URL HTTPS /social/ do Mercado Livre; ausente ou vazio usa o padrão acima.
Não acrescenta o parâmetro ref específico do produto compartilhado anteriormente.

O limite de legenda é calculado incluindo a URL: acima de 1024 usa texto único;
acima de 4096 bloqueia sem dividir nem cortar. O botão VER OFERTA dos produtos
continua no fluxo próprio. Reinicie monitor e publicador após atualizar.
