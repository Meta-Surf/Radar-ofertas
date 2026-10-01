# Radar Shopee — operação atual

## Modo integrado

O fluxo de produção usa:

```powershell
py -u radar_shopee_continuo.py --enfileirar --loop --intervalo 1200 --limite 3
```

ou simplesmente `INICIAR_INTEGRADO.bat`.

O catálogo `radar_categorias.json` possui atualmente **43 temas**, distribuídos em 6 grupos. As regras de título em `radar_shopee_continuo.py` reduzem acessórios/falsos positivos e a tabela `marcas_radar.json` adiciona prioridade controlada para marcas relevantes.

O radar enfileira candidatos; quem publica é `bot_ofertas_revisao.py`. O publicador respeita um relógio próprio de **1200 segundos (20 minutos)** somente para ofertas com origem `shopee_api`.

Ofertas captadas dos grupos/canais, recuperadas e cupons usam seus próprios critérios de prioridade e não reiniciam esse relógio.

## Critérios

Os temas podem definir:

- preço mínimo;
- desconto mínimo;
- avaliação mínima;
- vendas mínimas;
- termos premium;
- grupo/categoria.

A pontuação combina marca, premium, loja oficial quando disponível, avaliação, vendas e desconto. O ranking organiza candidatos; não comprova menor preço de mercado.

## Histórico

Preços publicados confirmados podem ser registrados no histórico. Selos de 15/30/45/60/.../180 dias exigem comparação válida e variante confirmada. Não habilite `variant_verified` por inferência de título.

## Diagnóstico

Prévia sem publicar:

```powershell
py radar_shopee_continuo.py --limite 3
```

Consulta direta por termo:

```powershell
py radar_shopee.py --buscar "fone bluetooth"
```

A consulta direta é útil para diagnóstico. O modo operacional recomendado é o integrado com `--enfileirar`.

## Modo legado direto

`radar_shopee_continuo.py --publicar` ainda existe para uso isolado, mas não deve rodar junto do fluxo integrado. Usar o radar direto e o publicador unificado ao mesmo tempo pode quebrar a prioridade e o controle esperado.

## Cupons e preço final

O radar não presume cupons, frete ou desconto personalizado. O preço da API não deve ser transformado em estimativa de checkout. Mensagens exclusivas de cupons vêm do monitor Telegram e seguem fluxo separado.

## Limites

- o radar não cobre todo o catálogo;
- regras de título são heurísticas;
- variações podem ter preços diferentes;
- estoque e preço final do carrinho podem mudar;
- respostas reais da API precisam ser validadas no computador com as credenciais configuradas.
