# Integração — Telegram, Shopee, Mercado Livre e filas

Atualizado em 30/09/2026. Para operação diária, veja também [docs/operacao.md](docs/operacao.md).

## Fluxo integrado

`INICIAR_INTEGRADO.bat` inicia:

```powershell
py -u monitor_ofertas.py
py -u radar_shopee_continuo.py --enfileirar --loop --intervalo 1200 --limite 3
py -u bot_ofertas_revisao.py
```

O catálogo atual possui **43 temas**. O relógio de **20 minutos** se aplica somente às publicações originadas no Radar Shopee.

## Prioridade

1. ofertas novas dos grupos/canais;
2. ofertas recuperadas;
3. Radar Shopee.

Uma publicação de grupo/canal não reinicia o relógio do radar.

## Preços

O monitor reconhece formatos explícitos como:

- `R$ 2.713,08 no Pix`;
- `💵2,943`;
- `Preço: R$ 2391`;
- `De R$ 3000 por R$ 2391`.

Não calcula preço total a partir de parcelas e não inventa valor final de checkout.

## Cupons

### Cupom dentro de oferta de produto

Permanece na própria oferta. Exemplo:

```text
R$ 233,00 com cupom: CODIGO
```

Isso **não** gera uma segunda publicação.

### Mensagem exclusiva de cupons

Gera o alerta visual próprio:

- Shopee: arte e links convertidos pelo fluxo Shopee;
- Mercado Livre: arte Mercado Livre, códigos/condições e Social configurado.

## Canal especial em modo espelho

Configuração validada:

```env
TG_ESPELHO_CHATS=-1003781163851
```

O canal especial:

- preserva texto, emojis, formatação, preço, condições e imagem;
- substitui o link de produto pelo link próprio de afiliado;
- remove redes sociais e páginas informativas;
- resolve links de loja e conserva somente os que identificam produto;
- descarta, por exemplo, links promocionais Shopee VIP sem produto;
- bloqueia publicação quando o produto/link afiliado não puder ser confirmado.

O canal é incluído automaticamente no monitor mesmo sem duplicar o ID em `TG_CHATS`.

## Mercado Livre

Ofertas de canais monitorados podem seguir:

`origem -> produto canônico -> leitura/complementação -> geração de afiliado -> Telegram`

A entrada manual definida por `ML_MANUAL_CHAT` é diferente: nela o link inserido pelo operador já é considerado seu próprio link e é preservado.

## Recuperação

O monitor reprocessa por padrão mensagens recentes ao reiniciar. Álbuns e edições são considerados e o digest da captura reduz duplicações.

## Histórico e deduplicação

`publicacoes.sqlite3` é compartilhado pelos fluxos integrados. Não apague o banco para forçar republicação.

O histórico de preços pode acumular registros, mas selos de 30/60/90/180 dias exigem comparação válida e variante confirmada.

## KaBuM

KaBuM/Awin permanece fora do publicador integrado nesta etapa. `radar_kabum.py` está em modo diagnóstico e mantém seu próprio cache/histórico. Veja [KABUM_AWIN.md](KABUM_AWIN.md).

## Testes

```powershell
py -m unittest discover -p "test_*.py" -v
```

Os testes automatizados usam serviços simulados; validação real de APIs/sessões deve ser feita no computador de execução.
