# Análise da auditoria externa

Data: 25/09/2026. Base conferida: `Meta-Surf/Radar-ofertas`, commit `db231eb2200641c58c2ebb8cfb7c2c8bad06ffe4`. Auditoria recebida: `auditoria-radar-ofertas.md`.

## Parecer

A priorização Telegram → WhatsApp → Instagram é adequada. O fallback para link comum do Mercado Livre foi confirmado no código, assim como a duplicação dos modelos de ambiente e a ausência de testes dedicados ao ML. O documento não deve ser aplicado integralmente sem os ajustes abaixo.

| Sugestão | Avaliação e decisão |
|---|---|
| Mercado Livre sem publicação até resolver afiliados/acesso | Aplicada opção (b): todos os modos apenas monitoram; função de envio bloqueada. Link mapeado sozinho não prova afiliação real. |
| Testes mockados ML | Aplicados 14 testes, incluindo autenticação, renovação, 403 geral e individual e bloqueio de publicação com/sem afiliado. Com envio desativado, testamos que nem uma resposta incerta pode ocorrer: nenhuma requisição Telegram sai. Testes de entrega real e estado incerto serão necessários antes de reativar. |
| Consulta manual fora do CI | Aplicada proteção de execução, além da documentação; importar o módulo não consulta a API. O CI já seleciona `test_*.py`. |
| Categorizar precisamente cada 403 | Parcial: log distingue acesso negado, inclusive por item, mas mantém causa indeterminada. Código HTTP genérico não identifica escopo, propriedade do item ou ID incorreto. Diagnóstico real permanece pendente. |
| Preço e cupom sem validação | Válido revisar, mas é necessário distinguir dados da API e da mensagem. O texto da auditoria não corresponde integralmente à legenda atual: ela diz “sujeitos a alteração”, sem rotular toda a origem. Não afirmar que todos os preços deixam de ser consultados nem que consultar a API valida cupom. |
| Dedupe por título/preço | Adiar bloqueio automático: variantes, kits e vendedores distintos podem parecer iguais. Começar com sinalização e medir falsos positivos. |
| Um modelo de ambiente | Aplicado: `.env.example`; arquivo duplicado removido e referências atualizadas. |
| Versões e hashes | Válido, pendente: declarar suporte Python e gerar lock verificável para Windows/Linux, incluindo transitivas. `python_requires` sozinho não fixa o interpretador em um projeto executado como scripts. |
| Interface sucesso/falha | Ajustar antes de implementar: incluir resultado incerto para timeout/resposta ambígua. Liberar reserva em toda “falha” pode duplicar publicações. |
| Dedupe entre canais | Reutilizar a infraestrutura, mas separar identidade de produto de entrega por canal/destino. Dedupe global de publicação impediria enviar a mesma oferta ao Telegram e WhatsApp. |
| WhatsApp sem API oficial de grupos | Afirmação desatualizada: Meta documenta Groups API, com elegibilidade e restrições. Existência da API não significa que a conta ou grandes grupos de ofertas sejam atendidos. Verificar antes de escolher integração. |
| Aquecimento de número | Não adotar como garantia contra bloqueio ou substituto das regras da plataforma. A abordagem WhatsApp permanece sem escolha. |
| Instagram exige sempre Página Facebook | Incorreto como regra geral: Instagram API with Instagram Login dispensa Página vinculada; requisitos dependem da modalidade de login. |
| Instagram separado e com curadoria | Adotado no plano; frequência será uma decisão editorial, sem afirmar penalidade automática de alcance sem evidência. |

## Evidência e limites deste lote

56 testes passaram localmente em Python 3.12.14/Linux, usando mocks nas integrações. A execução não usou credenciais reais, não publicou ofertas e não resolve os 403 observados na conta. O CI do repositório usa Python 3.14; o resultado remoto deve ser conferido no PR. O fluxo em produção e a comissão dos links continuam sem comprovação neste lote.

## Referências oficiais consultadas

A consulta encontrou documentação oficial indexada; a abertura integral das páginas Meta retornou HTTP 429. Por isso, não fixamos limites numéricos nem concluímos elegibilidade da conta.

- [WhatsApp Groups API](https://developers.facebook.com/documentation/business-messaging/whatsapp/groups)
- [Groups API — primeiros passos e elegibilidade](https://developers.facebook.com/documentation/business-messaging/whatsapp/groups/get-started/)
- [Instagram API with Instagram Login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/)

O acompanhamento operacional está em [plano-fases.md](plano-fases.md).
