# Continuidade do projeto — 30/09/2026

Este arquivo registra o ponto operacional atual. Documentos de correções datadas anteriores devem ser tratados como histórico; para iniciar o sistema use [docs/operacao.md](docs/operacao.md).

## Estado validado

- Repositório: `Meta-Surf/Radar-ofertas`, branch operacional `main`.
- Destino Telegram: `@ofertasbrasil_shopee`.
- Monitor Telegram com recuperação de mensagens recentes e captura de edições.
- Canal especial em modo espelho validado com ID `-1003781163851`.
- Radar Shopee com 43 temas e intervalo de publicação de 1200 segundos (20 minutos).
- Ofertas de grupos/canais têm prioridade e não reiniciam o relógio do radar.
- Mensagens de produto com código de cupom permanecem como uma única oferta.
- Somente mensagens exclusivas de cupons Shopee/Mercado Livre geram publicação própria com arte.
- Mercado Livre possui fluxo automático de afiliado para ofertas monitoradas e fluxo manual separado.
- KaBuM/Awin está em diagnóstico: feed, cache e histórico de preços funcionam; publicação ainda não está ligada.
- Amazon, Instagram e WhatsApp continuam pendentes.

## Dados que ficam apenas no computador

Preserve fora do Git:

- `.env` e todas as credenciais;
- `monitor_ofertas.session`;
- `publicacoes.sqlite3` e WAL/SHM;
- filas `*.jsonl`;
- `monitor_recuperacao.json`;
- `imagens_ofertas/`;
- Cookie/CSRF do Mercado Livre;
- URL privada do feed Awin;
- `kabum_historico.sqlite3` e caches/feeds KaBuM.

O GitHub não substitui esses dados operacionais.

## Regras funcionais principais

- preço deve ser explícito; não estimar checkout, Pix, parcelas ou cupons;
- parcelas nunca podem ser confundidas com o preço total;
- links de terceiros não podem ser publicados como fallback;
- ofertas captadas dos grupos/canais têm prioridade sobre o radar;
- histórico de publicações deve ser preservado para evitar repetição;
- conteúdo protegido contra encaminhamento não deve ser copiado;
- canal espelho remove redes sociais e páginas informativas e mantém somente links de produto convertíveis.

## Retomada

Na pasta do projeto:

```powershell
py -m pip install -r requirements.txt
py -m unittest discover -p "test_*.py" -v
INICIAR_INTEGRADO.bat
```

Antes de substituir arquivos em uma instalação existente, preserve os dados locais listados acima.

## Próximas frentes

1. acompanhar estabilidade das publicações reais e corrigir casos de parsing/link com exemplos concretos;
2. evoluir o ranking e a publicação do KaBuM somente depois de acumular histórico confiável;
3. integrar Amazon;
4. preparar publicação multicanal para WhatsApp e Instagram sem duplicar a lógica de seleção.
