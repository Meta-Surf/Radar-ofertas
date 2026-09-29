> Atualização de 27/09/2026: o fluxo atual usa **INICIAR_INTEGRADO.bat**, com grupos/cupons prioritários, radar a cada 600 segundos, 24 temas, marcas preferenciais e fila SQLite de duas horas. Preços divulgados passam a ser registrados após envio confirmado. Os selos de 30/60/90/180 dias exigem variante confirmada, ainda não fornecida pelas fontes atuais. Veja [ATUALIZACAO_RADAR.md](ATUALIZACAO_RADAR.md) para instalação e limites. Orientações históricas divergentes abaixo não descrevem o fluxo integrado atual.

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
| Afiliados Mercado Livre | Integração experimental pronta: gera short_url pela sessão autenticada do Link Builder; exige teste real local antes de habilitar |
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

Se já possui `.env`, preserve-o e não execute o comando de cópia sobre ele. Preencha as credenciais no arquivo local. O `.env.example` contém apenas nomes e padrões sem segredos. Para compatibilidade com as instruções anteriores, também existe `.env.exemplo`.

## Radar direto Shopee

Busca ofertas pela API sem depender dos grupos. Comece com `py radar_shopee.py --buscar "fone bluetooth"` (prévia sem publicação). Veja [filtros, simulação e execução automática](docs/radar-shopee.md). Para o radar contínuo de 22 temas com publicação de até uma oferta a cada cinco minutos, execute `py radar_shopee_continuo.py --publicar --loop --intervalo 300 --limite 1`. Preserve `.env` e `publicacoes.sqlite3` ao atualizar o projeto. A consulta de campanhas não valida códigos de cupom.

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
- `radar_mercadolivre_v6.py`: monitor de anúncios/IDs configurados. Comece com `--probe ID_DO_ANUNCIO` ou `--dry-run`; mantenha `ML_MODO_TESTE=1` durante os testes.
- Copie `examples/ml_itens.example.txt` para `ml_itens.txt` e `examples/ml_links_afiliados.example.json` para `ml_links_afiliados.json` para preencher seus dados localmente.

A autenticação OAuth continua separada da geração de afiliados. O fluxo integrado novo está em `mercadolivre_afiliados.py`: usa Cookie/X-CSRF-Token do Link Builder, bloqueia em caso de falha e nunca cai para link comum ou de terceiro. Veja [MERCADO_LIVRE_AFILIADOS.md](MERCADO_LIVRE_AFILIADOS.md) antes do primeiro teste real.

## Testes

```powershell
.\.venv\Scripts\python.exe -m unittest discover -p "test_*.py" -v
```

A suíte inclui testes do monitor, da integração de afiliados e do radar direto Shopee. Usam respostas simuladas; não fazem compras nem publicações reais. Essa cobertura se concentra nos componentes de monitoramento/Shopee; não comprova o funcionamento real do radar Mercado Livre.

## O que fica fora do Git

O `.gitignore` exclui credenciais, sessão do Telegram, filas, banco de publicações, fotos captadas, tokens, arquivos locais de anúncios e links, caches e arquivos compactados de backup. O logotipo `assets/radar_de_ofertas_menor_1mb.png` está incluído como recurso do projeto.

O arquivo `.session` permite acesso à conta Telegram. Não faça upload do pacote original do projeto para o GitHub. Se alguma credencial ou sessão tiver sido publicada, revogue-a e gere outra; apagar o arquivo do último commit não remove o histórico anterior.

## Atualizações

Depois de configurar o repositório remoto, mantenha esta estrutura de arquivos e envie apenas alterações revisadas de código e documentação. Não habilite envios de ofertas por workflows do GitHub: os testes automatizados não precisam de tokens reais.


## Cupons e publicação integrada

Consulte [INTEGRACAO.md](INTEGRACAO.md) para instalar os alertas de cupons,
prioridade das ofertas dos grupos e radar com intervalo de 10 minutos.
Diagnóstico sem envio: `py cupons_shopee.py --testar "URL_DO_CUPOM"`.


## Banner incluído no GitHub

A arte original está em `assets/banner_cupons.parts/`, dividida em partes Base64
para evitar a falha do envio binário pela integração. O bot restaura automaticamente
`assets/banner_cupons.png` quando necessário, verificando tamanho e SHA-256.
Não há alteração de pixels ou dependência de download. Para restaurar manualmente:
`py banner_asset.py`. Preserve a pasta de partes ao copiar o projeto.

Incluídas as correções de `**R$ 2.713,08**` e `💵2,943`, captura de edições,
bloqueio de ofertas sem preço, valor em negrito e condições em linha separada.
