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
- KaBuM/Awin 2.0 está em produção: feed, histórico, ranking, Link Builder, Offers API e cupons oficiais integrados.
- Amazon Creators API está implementada em modo fail-closed e aguarda Partner Tag/Credential ID/Credential Secret.
- Instagram e WhatsApp continuam pendentes.

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

1. acompanhar a Resiliência ML e ampliar a taxa de resolução sem contornar bloqueios do site;
2. cadastrar as credenciais Amazon Creators API para ativar a integração já implementada;
3. acompanhar a disponibilidade de uma fonte oficial de estoque KaBuM;
4. configurar `TELEGRAM_ADMIN_CHAT` se desejado para alertas privados de saúde;
5. preparar publicação multicanal para WhatsApp e Instagram sem duplicar a lógica de seleção.
