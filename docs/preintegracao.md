# Pré-integração multicanal

## Objetivo

Validar capacidade, idempotência, latência e qualidade do payload antes de permitir qualquer chamada de publicação para Instagram ou WhatsApp.

Nesta fase, `RADAR_DESTINOS_ATIVOS=telegram` continua sendo a única saída real. `RADAR_DESTINOS_SHADOW=instagram` gera somente candidatos locais em `delivery_shadow`.

## Barreiras de segurança

- `instagram_adapter.py` não possui cliente HTTP, token Meta ou chamada de API;
- `publish()` falha deliberadamente;
- links de afiliado do Telegram são removidos do candidato Instagram;
- o payload recebe `affiliate_state=DESTINATION_ATTRIBUTION_REQUIRED`;
- somente entregas Telegram confirmadas alimentam o shadow em produção;
- falha no shadow é fail-open para Telegram e nunca desfaz uma publicação já confirmada;
- Outbox real mantém claim atômico e um `SENDING` antigo vira `UNCERTAIN` após reinício, nunca retry automático;
- uma entrega `FAILED/SKIPPED` só volta a `PENDING` quando existe nova revisão do payload.

## Teste de carga de 2026-10-02

Executado na VPS com:

```bash
./.venv/bin/python validar_preintegracao.py --items 5000
```

Resultado observado:

- 5.000 enqueues Outbox em 62,4 ms (~80.088/s);
- 500 claims em 4,3 ms (~115.717/s);
- 2.000 candidatos shadow preparados em 176,0 ms (~11.364/s);
- double claim bloqueado;
- crash após claim reconciliado para `UNCERTAIN`;
- publicação externa permaneceu desabilitada;
- link afiliado Telegram não foi reutilizado no candidato Instagram.

Esses números medem somente o núcleo local SQLite/Python e não representam latência futura da API do Instagram.

## Amostra shadow real

A amostra inicial foi ampliada para 29 entregas Telegram enriquecidas e gerou 29 candidatos Instagram `READY`, sem chamada de rede. Cupons usam o banner próprio já existente no Radar.

Após o início das métricas reais, o primeiro recorte observado registrou:

- `monitor.capture`: p95 375,0 ms em 23 amostras;
- `publisher.gate`: p95 1.379,3 ms em 18 amostras;
- `publisher.telegram_send`: p95 1.194,2 ms em 4 amostras.

As amostras ainda são iniciais; a avaliação deve continuar por pelo menos um ciclo operacional completo antes de ativar uma API externa.

## Critérios internos antes de publicar no Instagram

1. manter o shadow ativo por pelo menos 24 horas e acumular amostra representativa;
2. investigar qualquer candidato `BLOCKED` e buscar taxa residual inferior a 5%;
3. manter p95 de captura abaixo de 5 s e Gate abaixo de 10 s;
4. validar atribuição específica por destino para cada loja antes de qualquer CTA de compra;
5. validar mídia, legenda e política de links em ambiente de teste da integração oficial escolhida;
6. implementar no adaptador real idempotência por external ID, rate-limit, backoff, circuit breaker e reconciliação de resultado incerto;
7. somente então mover `instagram` para `RADAR_DESTINOS_ATIVOS`.

Nenhum desses passos requer desligar o Telegram. A ativação externa deve ser uma mudança separada, reversível e precedida por backup.
