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

`cupons_shopee.py` e `cupons_mercadolivre.py` tratam mensagens dedicadas a cupons. `cupons_kabum.py` trata vouchers oficiais genéricos vindos da Offers API Awin. Um código vinculado diretamente a produto permanece associado somente àquela oferta.

## KaBuM / Awin

`radar_kabum.py` integra o ciclo de produção e não envia diretamente ao Telegram. Ele:

- atualiza Product Feed/cache/histórico KaBuM;
- identifica quedas comprovadas e monta ranking;
- consulta Offers API e associa promoções somente quando há vínculo comprovado;
- enfileira vouchers genéricos elegíveis com identidade `KaBuMCoupon:<promotion_id>`;
- usa a mesma fila, ledger, relógio de 20 minutos e publicador unificado dos demais radares.

O Link Builder Awin é fallback para destinos KaBuM válidos que não tenham tracking disponível no feed.

## Gate central pré-publicação

`prepublicacao.py` é a última barreira antes de `Ledger.reserve()` e do relógio de publicação. Ele recebe a oferta original e a versão preparada pelo afiliado, revalida o que cada fonte consegue comprovar e só então libera a reserva.

- Radar Shopee: preço/atividade são reconsultados pela API;
- Shopee de grupo: identidade/link são revalidados; preço final com cupom/Pix só é aceito da origem por até 5 minutos;
- Mercado Livre automático: uma leitura pública recente confirma produto, preço total e disponibilidade; parcela nunca vira preço;
- Mercado Livre manual: o link autorizado é preservado e preço explícito recente pode ser aceito por até 5 minutos;
- KaBuM: o Product Feed precisa estar recente; preço maior bloqueia, preço menor atualiza; `out_of_stock` explícito bloqueia;
- cupom KaBuM: `promotion_id` é reconsultado na Offers API antes do envio;
- cupons Shopee/ML sem API de validade equivalente: alertas antigos demais são bloqueados.

A tabela `prepublication_gate` em `publicacoes.sqlite3` registra `APROVADA`, `ATUALIZADA` ou `BLOQUEADA` e motivos como `PRECO_AUMENTOU`, `CUPOM_EXPIRADO`, `LINK_INVALIDO`, `DUPLICADA` e `SEM_ESTOQUE_COMPROVADO`. Uma rejeição ocorre antes de `mark_attempt`, portanto não gasta o slot de 20 minutos.

## Persistência

`publicacoes.sqlite3` armazena reservas, publicações, relógios, auditoria do Gate e dados usados pela inteligência/histórico. Filas, banco, sessão, mídia e caches são locais e ignorados pelo Git.

## Testes

GitHub Actions executa `python -m unittest discover -p 'test_*.py' -v` sem credenciais reais. Integrações externas devem falhar de forma fechada: ausência de autenticação ou resolução confiável impede publicação.
