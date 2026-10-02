# Histórico do Mercado Livre

Esta pasta contém implementações antigas ou experimentais preservadas para consulta. Nenhum arquivo daqui participa dos serviços de produção atuais.

O fluxo ativo do Mercado Livre está dividido entre:

- `../../mercadolivre_afiliados.py` — geração de link afiliado;
- `../../mercadolivre_auto.py` — leitura pública/complementação;
- `../../mercadolivre_manual.py` — grupo manual autorizado;
- `../../mercadolivre_resiliencia.py` — backoff/quarentena/circuit breaker;
- `../../mercadolivre_session_health.py` — saúde da sessão de afiliado;
- `../../cupons_mercadolivre.py` — cupons;
- `../../bot_ofertas_revisao.py` — publicação unificada.

Arquivados nesta pasta:

- `radar_mercadolivre*.py` — gerações anteriores do radar standalone, incluindo a antiga v6;
- `mercadolivre_auth_oauth.py` — utilitário OAuth experimental, não usado pelo Link Builder;
- `teste_mercadolivre_real.py` — teste manual que acessa API real e nunca faz parte da suíte automática.

Não execute scripts históricos junto dos serviços de produção. Para investigar uma versão, use uma cópia isolada do projeto e nunca reutilize credenciais/dados reais sem necessidade.
