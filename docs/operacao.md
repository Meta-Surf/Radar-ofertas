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
