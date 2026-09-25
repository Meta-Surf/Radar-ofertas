# Publicador de afiliados Shopee

## O que mudou

O monitor continua identificando ofertas no Telegram. O publicador usa suas credenciais da API de Afiliados da Shopee para gerar um novo link antes de enviar a oferta. O botão usa exatamente o link retornado por essa requisição autenticada. Se não conseguir gerar um link válido, não publica. A consulta opcional de produto traz nome e imagem da API, quando disponíveis, usando os IDs exatos de loja e produto.

A conta das comissões é a conta à qual o App ID e o Secret pertencem. Um link criado com sucesso não garante comissão: a venda precisa ser elegível, atribuída e validada pela Shopee. Use apenas canais de divulgação aceitos e cadastrados no seu programa de afiliados. Confira os resultados no painel da Shopee.

Amazon e Mercado Livre continuam na captura, mas o publicador os deixa pendentes até que suas respectivas integrações de afiliados estejam prontas. Nenhuma oferta dessas lojas é enviada com link comum.

## Instalação no Windows

1. Pare os dois programas com Ctrl+C. Faça backup dos scripts antigos e extraia TODOS os arquivos deste pacote na mesma pasta Telegram, substituindo os scripts. Não rode versões antigas do publicador em paralelo.
2. Preserve `.env`, `monitor_ofertas.session`, o banco e as filas locais. O pacote não contém nem substitui esses dados. Apenas `.env.example` é fornecido como referência.
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
- publicacoes.sqlite3 impede repetir o mesmo ID de produto no mesmo dia em America/Sao_Paulo, inclusive entre grupos e depois de reiniciar. Produtos já publicados hoje pela versão antiga também ficam bloqueados: não apague o banco para forçar republicação.
- Falha ao gerar o link libera a reserva porque nada foi enviado e aplica uma pausa de cinco minutos antes de novas tentativas. Se a resposta do Telegram for incerta (timeout/queda), mantém a reserva por aquele dia para evitar duplicidade. Consulte o canal nesses casos.
- Anúncios diferentes de um mesmo produto podem ter IDs distintos; não existe deduplicação visual ou por descrição.
- Não execute monitores simultâneos com a mesma sessão. Não use o banco simultaneamente via OneDrive em computadores diferentes. A fila, o banco e as imagens são locais e ocupam espaço; não há exclusão automática.

## Limitações que permanecem

Links curtos bloqueados com 403 podem continuar sem identificação, mesmo com as credenciais Shopee configuradas. A API de Afiliados não resolve bloqueios do Mercado Livre. A correspondência manual meli.la/2PnnX9t -> MLB4360643061 continua preservada. Conteúdo com vários produtos é ignorado para evitar associações incorretas. O diagnóstico não reprocessa mensagens antigas para publicação.

Para diagnosticar, pare o monitor antes de executar:

`py monitor_ofertas.py --diagnosticar https://t.me/sohardwaredorocha/18776`

O teste de geração de links deve receber um produto Shopee. Se ocorrer falha de autenticação, confira credenciais do painel de API de Afiliados e data/hora automática do Windows. Falhas são exibidas sem o conteúdo de cabeçalhos ou respostas que possam revelar segredos. A configuração não permite trocar o endpoint que recebe as credenciais.

## Validação técnica e fontes

Foram executados 33 testes locais com chamadas externas simuladas, cobrindo reconhecimento de links, duplicatas, assinatura dos bytes enviados, erro de autenticação, ausência de credenciais, rejeição de link inválido, botão com link afiliado e ausência de envio ao Telegram quando a API falha. Não houve uso de suas credenciais nem publicação real durante o desenvolvimento.

Execute `py -m unittest discover -v` para repetir os testes. Eles não usam suas credenciais reais.

A autenticação e o esquema generateShortLink(input: ShortLinkInput), originUrl, subIds e shortLink foram conferidos no HTML e no esquema incorporado dos exploradores oficiais em 25/09/2026:
- https://open-api.affiliate.shopee.com.br/explorer
- https://open-api.affiliate.shopee.com.br/explorer/v2
