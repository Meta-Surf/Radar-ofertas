# Arquitetura atual — 30/09/2026

## Visão geral

O projeto separa captura, descoberta, enriquecimento, geração de afiliado e publicação. O objetivo é impedir que uma falha em uma loja ou origem provoque publicação incorreta em outra.

## Telegram

`monitor_ofertas.py` usa Telethon para:

- monitorar `TG_CHATS` e `TG_ESPELHO_CHATS`;
- processar mensagens novas, álbuns e edições;
- recuperar mensagens recentes ao reiniciar;
- reconhecer ofertas e mensagens exclusivas de cupons;
- baixar mídia autorizada;
- gravar candidatos em `fila_ofertas_v2.jsonl`.

`CaptureRevisions` e `monitor_recuperacao.json` reduzem duplicação por reprocessamento.

### Modo espelho

`canal_espelho.py` preserva texto/formatação da origem especial, remove redes sociais/páginas informativas e substitui somente o link de produto. Links da mesma loja que não resolvem para produto, como páginas promocionais Shopee VIP, são descartados.

## Shopee

`radar_shopee_continuo.py` consulta os 43 temas definidos em `radar_categorias.json` e alimenta a fila persistente de inteligência.

`bot_ofertas_revisao.py` revalida ofertas, gera o link via `shopee_afiliados.py` e publica. O relógio exclusivo do radar é de 1200 segundos. Ofertas dos grupos/canais não aguardam esse relógio.

## Mercado Livre

Fluxos atuais:

1. **grupos monitorados**: o link é normalizado para um produto e `mercadolivre_afiliados.py` tenta gerar o novo link pela sessão do Link Builder;
2. **entrada manual**: `mercadolivre_manual.py` preserva o link fornecido pelo operador no grupo autorizado;
3. **leitura automática**: `mercadolivre_auto.py` tenta completar título, preço e imagem sem usar parcela como preço total;
4. **cupons**: `cupons_mercadolivre.py` monta a publicação própria de listas exclusivas.

O OAuth em `mercadolivre_auth.py` permanece útil para experimentos/API, mas não é o mecanismo usado pela geração de afiliado.

## Cupons

`cupons_shopee.py` e `cupons_mercadolivre.py` são acionados somente para mensagens dedicadas a cupons. Um código de cupom dentro de uma oferta de produto permanece no texto da própria oferta e não gera outra postagem.

## KaBuM / Awin

`radar_kabum.py` é independente do publicador principal nesta fase. Ele:

- aceita Product Feed direto ou Product Feed List;
- seleciona o feed KaBuM;
- mantém cache local;
- grava produtos e histórico em SQLite;
- identifica quedas comprovadas.

O módulo ainda não envia ao Telegram.

## Persistência

`publicacoes.sqlite3` armazena reservas, publicações, relógios e dados usados pela inteligência/histórico. Filas, banco, sessão, mídia e caches são locais e ignorados pelo Git.

## Testes

GitHub Actions executa `python -m unittest discover -p 'test_*.py' -v` sem credenciais reais. Integrações externas devem falhar de forma fechada: ausência de autenticação ou resolução confiável impede publicação.
