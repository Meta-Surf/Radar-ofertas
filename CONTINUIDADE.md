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

O banner aprovado ainda precisa ser associado ao arquivo correto e instalado em assets/banner_cupons.png ou CUPONS_BANNER. Sem banner, alertas usam texto e botões. Mercado Livre continua com acesso a itens bloqueado por 403 nos testes históricos; Amazon e KaBuM não têm integração confirmada.

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

Identificar o banner aprovado; validar preço de novas mensagens e conversão real de cupons. Trabalhar em tarefas delimitadas, registrar testes, commit e pendências a cada entrega. Não recomeçar o projeto.
