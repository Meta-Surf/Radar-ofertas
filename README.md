# Radar de Ofertas

Monitor de ofertas do Telegram e publicador de links de afiliado da Shopee. Inclui os experimentos do radar de preços e autenticação do Mercado Livre.

## Estado do projeto

| Componente | Situação |
|---|---|
| Monitor de grupos/canais do Telegram | Implementado; utiliza a conta do usuário e chats configurados |
| Geração de links Shopee | API integrada; geração real de link confirmada pelo usuário |
| Publicador Shopee | Implementado; bloqueia envio sem link de afiliado gerado |
| Imagens, cupons e duplicatas | Implementados com as limitações descritas em [docs/operacao.md](docs/operacao.md) |
| Mercado Livre | OAuth e versões de radar incluídos; consultas a alguns itens apresentaram HTTP 403 |
| Afiliados Mercado Livre | Radar v6 lê links já gerados de um arquivo local; não gera novos links automaticamente |
| Amazon, Instagram e WhatsApp | Integrações de publicação/afiliados pendentes |

Este repositório preserva o código recebido em 25/09/2026. As versões anteriores do radar ficam em `archive/mercadolivre/`. A versão atual, `radar_mercadolivre_v6.py`, permanece na raiz. Elas não representam commits históricos: o histórico do Git começa na importação.

## Organização

| Pasta/arquivo | Finalidade |
|---|---|
| Scripts Python na raiz | Monitor, publicador e integrações atuais |
| `tests/` | Testes locais com serviços simulados |
| `docs/` | Operação e arquitetura |
| `examples/` | Modelos de arquivos locais do Mercado Livre |
| `assets/` | Identidade visual |
| `archive/mercadolivre/` | Versões anteriores para consulta |
| `.github/workflows/` | Validação automática em cada atualização |

Veja também [arquitetura](docs/arquitetura.md) e [como contribuir](CONTRIBUTING.md).

## Configuração no Windows

Recomendado: Python 3.11 ou superior. O projeto foi utilizado pelo usuário no Python 3.14.

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Se já possui `.env`, preserve-o e não execute o comando de cópia sobre ele. Preencha as credenciais no arquivo local. O `.env.example` contém apenas nomes e padrões sem segredos. `.env.example` é o único modelo de configuração.

## Radar direto Shopee

Busca ofertas pela API sem depender dos grupos. Comece com `py radar_shopee.py --buscar "fone bluetooth"` (prévia sem publicação). Veja [filtros, simulação e execução automática](docs/radar-shopee.md). A consulta de campanhas não valida códigos de cupom.

## Shopee + Telegram

Teste a geração de um link com uma URL real de produto:

```powershell
.\.venv\Scripts\python.exe shopee_afiliados.py --testar "URL_DO_PRODUTO_SHOPEE"
```

Rode o monitor em uma janela:

```powershell
.\.venv\Scripts\python.exe monitor_ofertas.py
```

Em outra janela na mesma pasta, simule as ofertas elegíveis:

```powershell
.\.venv\Scripts\python.exe bot_ofertas_revisao.py --simular
```

Para publicar automaticamente, execute sem `--simular`. A simulação consulta a API de Afiliados, mas não envia mensagens ao Telegram. O monitor captura mensagens novas; o diagnóstico não coloca mensagens antigas na fila. Consulte [o guia de operação](docs/operacao.md) para fotos, cupons, resolução de links, horário de Brasília e limites do controle de duplicatas.

## Mercado Livre

- `mercadolivre_auth.py`: fluxo OAuth com callback local. Usa Flask; exibe tokens no terminal após autorização. Não compartilhe a saída. É um utilitário de desenvolvimento, não um servidor de produção.
- `teste_mercadolivre.py`: consulta real à conta configurada. Não faz parte dos testes automatizados e sua saída pode conter dados pessoais.
- `radar_mercadolivre_v6.py`: monitor de anúncios/IDs configurados. Todos os modos (inclusive `--loop`) apenas monitoram; não publicam no Telegram. Use `--probe ID_DO_ANUNCIO` para diagnosticar acesso.
- Copie `examples/ml_itens.example.txt` para `ml_itens.txt` e `examples/ml_links_afiliados.example.json` para `ml_links_afiliados.json` para preencher seus dados localmente.

A autenticação OAuth não garante acesso a todos os anúncios nem habilita uma API de geração de links de afiliado. Na Fase 0, o v6 fica restrito ao monitoramento manual. Links comuns ou mapeados são exibidos somente no terminal. Até chamadas diretas a `enviar_telegram` são bloqueadas por código.

## Testes

```powershell
.\.venv\Scripts\python.exe -m unittest discover -p "test_*.py" -v
```

A suíte inclui testes do monitor, da integração de afiliados e do radar direto Shopee. Usam respostas simuladas; não fazem compras nem publicações reais. O Mercado Livre também tem testes mockados de autenticação, HTTP 403 e bloqueio de publicação. Isso não comprova acesso real às APIs nem comissão dos links mapeados.

## O que fica fora do Git

O `.gitignore` exclui credenciais, sessão do Telegram, filas, banco de publicações, fotos captadas, tokens, arquivos locais de anúncios e links, caches e arquivos compactados de backup. O logotipo `assets/radar_de_ofertas_menor_1mb.png` está incluído como recurso do projeto.

O arquivo `.session` permite acesso à conta Telegram. Não faça upload do pacote original do projeto para o GitHub. Se alguma credencial ou sessão tiver sido publicada, revogue-a e gere outra; apagar o arquivo do último commit não remove o histórico anterior.

## Atualizações

Depois de configurar o repositório remoto, mantenha esta estrutura de arquivos e envie apenas alterações revisadas de código e documentação. Não habilite envios de ofertas por workflows do GitHub: os testes automatizados não precisam de tokens reais.

## Acompanhamento por fases

Consulte [o checklist do projeto](docs/plano-fases.md) e [a análise da auditoria](docs/analise-auditoria.md). Marque itens somente após implementação e verificação; valide operação real separadamente.
