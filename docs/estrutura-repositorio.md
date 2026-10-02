# Estrutura do repositório

## Entradas de produção

- `monitor_ofertas.py` — captura Telegram e grava `captured_queue`.
- `radar_shopee_continuo.py` — descobre candidatos Shopee/KaBuM e alimenta `radar_queue`.
- `bot_ofertas_revisao.py` — prioriza, revalida, gera afiliado e publica.
- `radar_health.py` — diagnóstico operacional e alertas administrativos.
- `deploy/bin/radar-backup.sh` — backup dos dados mutáveis.
- `deploy/systemd/` — units usados pela VPS.

## Núcleo compartilhado

- `configuracao.py` — defaults, limites e parsing de configuração.
- `ofertas_core.py` — identidade, preço, caption e ledger.
- `inteligencia_ofertas.py` — fila do radar, histórico e ranking.
- `fila_ofertas_sqlite.py` — fila capturada dos grupos.
- `prepublicacao.py` — Gate imediatamente antes do envio.
- `publicacao_oferta.py` — validação e envio Telegram de produto.
- `telegram_api.py` — classificação de respostas da Bot API.
- `publisher_backoff.py` — backoff persistente do publicador.
- `metricas_fontes.py` — métricas por origem.

## Integrações

Cada loja mantém módulos próprios para leitura/afiliado, sem importar código arquivado:

- Shopee: `shopee_afiliados.py`, `radar_shopee.py`, `cupons_shopee.py`.
- Mercado Livre: `mercadolivre_*.py` e `cupons_mercadolivre.py`.
- KaBuM/Awin: `radar_kabum.py`, `kabum_afiliados.py`, `awin_kabum.py`, `cupons_kabum.py`.
- Amazon: `amazon_afiliados.py`.

## Utilitários operacionais

Permanecem na raiz porque são comandos administrativos válidos:

- `consultar_historico.py`;
- `converter_link.py`;
- `reconciliar_reservas.py`;
- `relatorio_fontes.py`.

## Testes

Todos os testes automáticos ficam em `tests/` e são executados por:

```bash
python -m unittest discover -p 'test_*.py' -v
```

O CI usa o mesmo comando. Testes manuais que dependem de API real não pertencem à suíte.

## Histórico

`archive/` contém snapshots antigos ou experimentais. Eles não são importados pelos entrypoints de produção e não devem ser iniciados junto dos serviços atuais.

Arquivar significa preservar para consulta, não oferecer suporte operacional.
