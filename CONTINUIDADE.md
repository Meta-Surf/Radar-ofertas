> Atualização de 27/09/2026: o fluxo atual usa **INICIAR_INTEGRADO.bat**, com grupos/cupons prioritários, radar a cada 600 segundos, 24 temas, marcas preferenciais e fila SQLite de duas horas. Preços divulgados passam a ser registrados após envio confirmado. Os selos de 30/60/90/180 dias exigem variante confirmada, ainda não fornecida pelas fontes atuais. Veja [ATUALIZACAO_RADAR.md](ATUALIZACAO_RADAR.md) para instalação e limites. Orientações históricas divergentes abaixo não descrevem o fluxo integrado atual.

# Continuidade do projeto — 26/09/2026

## Ponto de partida

Repositório privado: https://github.com/Meta-Surf/Radar-ofertas
Conta proprietária: Meta-Surf. Branch de continuidade: main após incorporação da PR #2.
PR de integração: https://github.com/Meta-Surf/Radar-ofertas/pull/2
Commit de código validado: da708f26f760d02831cc765969c5a8d13ee020a7.

O usuário autorizou explicitamente atualizar a main antes da migração para um novo projeto de conversa. O arquivo bot.rar enviado em 26/09 contém a instalação parada pelo usuário. Os arquivos comuns à PR são equivalentes após normalizar finais de linha e espaços nas extremidades; o pacote local não contém tests/test_precos_grupos.py. Preservar a versão organizada e os testes da PR.

## Regras de operação

- Canal: @ofertasbrasil_shopee. Bot: @OfertasShopeeBrasl_bot.
- Ofertas e cupons dos grupos têm prioridade. Apenas o radar segue 600 segundos.
- Gerar links com a API afiliada do usuário; falhas de conversão bloqueiam a publicação.
- Preservar preços explícitos e condições, sem estimar descontos de checkout, Pix ou cupons.
- Manter Oferta na Shopee e botão Ver oferta, com texto profissional.
- Compartilhar histórico para evitar duplicatas. Nunca apagar o banco para repetir publicações.

## Estado e limites

O banco recebido passou em PRAGMA integrity_check e contém 91 registros na tabela posts, além de publication_clock. Configuração, sessão e filas foram encontradas no arquivo privado. Não enviar esses dados ao GitHub.

A PR inclui correção de preços dos grupos e integração de cupons. Os checks de GitHub Actions do commit acima concluíram com sucesso. Validação com APIs simuladas não equivale a publicação real. Nenhuma publicação real foi feita na migração.

O banner aprovado está incluído nas partes versionadas e é restaurado automaticamente em assets/banner_cupons.png; CUPONS_BANNER permite uma arte personalizada. Sem banner, alertas usam texto e botões. Mercado Livre continua com acesso a itens bloqueado por 403 nos testes históricos; Amazon e KaBuM não têm integração confirmada.

## Acesso e retomada

Acessar o repositório autenticado na conta Meta-Surf ou com uma conta autorizada. No novo ambiente, usar a conexão GitHub autorizada para este repositório; não colar tokens, senhas ou sessões em mensagens ou no código.

Para baixar uma cópia com Git instalado:

```powershell
git clone https://github.com/Meta-Surf/Radar-ofertas.git
cd Radar-ofertas
git switch main
py -m pip install -r requirements.txt
py -m unittest discover -s tests -v
```

A autenticação para o repositório privado deve ocorrer pelo mecanismo normal do Git/GitHub. Restaurar configuração, sessão, banco, filas e mídia do backup privado na pasta de execução. Ver INTEGRACAO.md antes de iniciar INICIAR_INTEGRADO.bat. Não executar publicadores antigos em paralelo. A atualização do GitHub não altera a instalação do Windows.

## Próxima tarefa

Validar preço de novas mensagens e conversão real de cupons. Trabalhar em tarefas delimitadas, registrar testes, commit e pendências a cada entrega. Não recomeçar o projeto.


## Banner incluído no GitHub

A arte original está em `assets/banner_cupons.parts/`, dividida em partes Base64
para evitar a falha do envio binário pela integração. O bot restaura automaticamente
`assets/banner_cupons.png` quando necessário, verificando tamanho e SHA-256.
Não há alteração de pixels ou dependência de download. Para restaurar manualmente:
`py banner_asset.py`. Preserve a pasta de partes ao copiar o projeto.

Incluídas as correções de `**R$ 2.713,08**` e `💵2,943`, captura de edições,
bloqueio de ofertas sem preço, valor em negrito e condições em linha separada.
