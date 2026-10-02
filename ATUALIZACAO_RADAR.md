> **DOCUMENTO HISTÓRICO (27/09/2026).** Não use os intervalos, número de temas ou passos de instalação abaixo como referência operacional atual. O sistema atual usa 43 temas e relógio de 1200 segundos (20 minutos) para o Radar Shopee. Veja [docs/operacao.md](docs/operacao.md).

# Atualização — marcas, fila e histórico — 27/09/2026

## Aplicação no Windows

1. Faça uma cópia da pasta do bot antes da atualização.
2. Feche monitor, radar e publicador com Ctrl+C. É necessário um reinício
   coordenado para carregar os módulos novos; não abra instâncias paralelas.
3. Extraia este ZIP na pasta onde fica INICIAR_INTEGRADO.bat, aceitando
   substituir os arquivos de código. Não apague os demais arquivos.
4. Preserve .env, monitor_ofertas.session, publicacoes.sqlite3 (e seus arquivos
   WAL/SHM, se houver), imagens e filas existentes. Não há cópias desses dados
   neste pacote. Se houver personalizações no código, concilie antes de substituir.
5. Verifique se TG_CHATS contém -1001764811887, separado dos IDs anteriores
   por vírgula. A atualização não escreve no seu .env.
6. Execute INICIAR_INTEGRADO.bat uma única vez. Use o modo integrado para
   manter prioridade dos grupos, fila persistente e envio a cada 600 segundos.
   Não execute também o radar com --publicar: esse é o publicador direto legado.

Nenhum processo do seu computador foi alterado ou reiniciado por esta entrega.
A atualização do repositório não altera automaticamente a instalação no Windows.

## Marcas e seleção

marcas_radar.json é a tabela editável por categoria. Todos os nomes têm o mesmo
bônus de 12 pontos; várias marcas no mesmo título não acumulam bônus.
A pontuação anterior de avaliação, vendas e desconto permanece; os critérios
mínimos de 20%, nota 4,5 e 50 vendas continuam. A marca não certifica autenticidade.
O título precisa corresponder à categoria; menções após “compatível”, “similar”,
“tipo”, “para” ou “vs” não dão bônus. Ryzen e Radeon identificam AMD; GeForce,
RTX e GTX identificam a preferência por NVIDIA no tema placas de vídeo.
Há 24 temas: foram acrescentados refrigeração e carregadores.
Alterações na tabela de marcas entram no próximo reinício.

## Fila

Todas as ofertas aprovadas do radar entram em radar_queue no mesmo banco local.
O ID loja/produto evita duplicatas. Cada consulta bem-sucedida renova a validade
para duas horas; ausência em uma busca ou falha de rede não apagam a fila.
Entradas expiradas são removidas. O JSONL antigo do radar deixa de ser a fonte
ativa; a primeira consulta do radar preenche a nova fila sem importar preços antigos.
As ofertas captadas dos grupos também usam SQLite (`captured_queue`); o JSONL antigo
é lido apenas uma vez para migração compatível e pode ser arquivado depois da validação.

O publicador atende grupos e cupons primeiro. O relógio de 1200 segundos se
aplica só ao radar. Antes do envio, a API revalida preço, filtros e imagem;
falha nessa preparação remove a candidata, permitindo tentar a próxima.
Categorias com pontuação a até cinco pontos da melhor alternam pela data de
última publicação. Há um bônus de 15 pontos para recordes comparáveis elegíveis.

## Histórico e limites de comparação

price_history registra preço em centavos, data/hora, canal, produto, mensagem,
condições e dados da oferta somente após confirmação do Telegram. A gravação
ocorre na mesma transação da confirmação no controle de publicações.
Falhas, simulações e envios incertos não contam como preços divulgados.
O banco recebido contém posts e publication_clock, mas não preços históricos:
não foram inventados ou importados valores para as divulgações antigas.

Janelas implementadas: 15, 30, 45, 60, ..., 180 dias. Mostra-se apenas o maior período
válido, exigindo acompanhamento desde o início da janela e pelo menos uma
publicação comparável dentro dela. Empates usam “Iguala o menor preço divulgado”;
quedas usam “Novo menor preço divulgado neste canal nos últimos N dias”.
Não há afirmação sobre menor preço de todo o mercado.

LIMITAÇÃO ATUAL: as fontes atuais não fornecem identificação confirmada da
variante. Por isso o histórico começa a acumular todos os preços, mas o selo
fica bloqueado nesses registros. Para habilitar comparação, uma integração deve
fornecer variant_id e variant_verified=true com evidência da variante efetivamente
ofertada. Não habilite esse campo por dedução do título ou por preço único.
Preço “a partir de” nunca gera selo. Canal, variante, origem, cupom e condição
precisam coincidir. Revalidação via API não conserva identificação antiga de
variante que a resposta nova não confirme. Essa restrição impede anunciar um
recorde falso de memória/capacidade/cor/kit diferentes. Não existe ativação
automática do selo apenas porque transcorreram 30 dias.

A repetição de um produto divulgado continua bloqueada, exceto quando houver
queda de preço comparável, variante confirmada e intervalo mínimo de 24 horas.
Reservas com resultado incerto permanecem bloqueadas, inclusive após reinício.
Registros antigos sem preço também mantêm o bloqueio anterior.

Consulta somente leitura, sem API e sem publicação:

    py consultar_historico.py
    py consultar_historico.py --produto Shopee:123:456

O selo aparece abaixo do valor destacado, com condições em linhas separadas.
O banner aprovado dos cupons e a correção de preços de 27/09 estão incluídos.

## Verificação

83 testes locais com APIs simuladas: fila/reinício/expiração, marcas, rotação,
prioridade dos grupos, revalidação inválida seguida por outra candidata,
janelas históricas, variantes, condições, transação e envio incerto.
Sem acesso às credenciais reais e sem envio ao Telegram.

## Banner incluído no GitHub

A arte original está em `assets/banner_cupons.parts/`, dividida em partes Base64
para evitar a falha do envio binário pela integração. O bot restaura automaticamente
`assets/banner_cupons.png` quando necessário, verificando tamanho e SHA-256.
Não há alteração de pixels ou dependência de download. Para restaurar manualmente:
`py banner_asset.py`. Preserve a pasta de partes ao copiar o projeto.

Incluídas as correções de `**R$ 2.713,08**` e `💵2,943`, captura de edições,
bloqueio de ofertas sem preço, valor em negrito e condições em linha separada.
