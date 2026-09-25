# Plano por fases — Radar de Ofertas

Atualizado em 25/09/2026. `[x]` significa implementado e verificado neste lote; `[ ]` significa pendente. Conclusão técnica não equivale a validação em produção. Este é o checklist principal para as próximas sessões.

## Fase 0 — Concluir Telegram

### Lote 1 — Proteção do Mercado Livre e organização

- [x] Comparar a auditoria com o código atual do GitHub.
- [x] Registrar os ajustes da auditoria em `docs/analise-auditoria.md`.
- [x] Aplicar opção (b): Mercado Livre somente como monitor manual, inclusive no modo contínuo.
- [x] Bloquear chamadas diretas ao envio Telegram do Mercado Livre, mesmo com link mapeado.
- [x] Exibir no terminal se o link é comum ou mapeado, sem prometer comissão.
- [x] Deixar o monitor independente das credenciais do Telegram.
- [x] Adicionar testes mockados de autenticação, renovação, 403 geral/individual e ausência de publicação.
- [x] Impedir consulta real de `teste_mercadolivre.py` ao importar o módulo e ao executar em CI.
- [x] Registrar 403 como acesso negado com causa indeterminada, sem inventar diagnóstico ou renovar token por 403.
- [x] Unificar configuração em `.env.example` e atualizar referências.
- [x] Executar a suíte: 56 testes passaram no Python 3.12.14, Linux; 14 novos testes Mercado Livre.

### Lote 2 — Qualidade da oferta e comprovação operacional

- [ ] Rastrear origem dos preços (mensagem/API), instante de consulta e tratamento de cupom; ajustar legenda sem afirmar validação inexistente.
- [ ] Manter identificação da loja, descrição profissional e botão VER OFERTA na revisão de legendas.
- [ ] Validar em execução real autorizada a descoberta Shopee → fila → afiliado → Telegram → registro, incluindo reinício.
- [ ] Confirmar que falha incerta de envio mantém reserva e não provoca republicação automática.
- [ ] Investigar o 403 com evidências sanitizadas da conta/recurso; a causa real ainda não está resolvida.
- [ ] Declarar versões Python suportadas com base no CI e validar Windows; o CI existente usa 3.14 e este lote foi testado localmente em 3.12.14.
- [ ] Gerar lock com hashes das dependências diretas e transitivas e testar instalação limpa nas plataformas suportadas.
- [ ] Avaliar similaridade somente como sinal para revisão, medindo falsos positivos antes de bloquear anúncios.

**Saída da fase:** Shopee validada de ponta a ponta sem intervenção manual no fluxo normal; Mercado Livre sem envio automático; cenários críticos de falha cobertos. A Fase 0 ainda não está concluída.

## Preparação para a Fase 1 — Arquitetura comum

- [ ] Definir contrato de publicação com resultado confirmado, rejeitado e incerto.
- [ ] Definir idempotência por produto + canal/destino + janela, mantendo seleção global de ofertas separada.
- [ ] Reservar antes do envio; confirmar no sucesso, liberar só na rejeição inequívoca, manter reserva em resultado incerto.
- [ ] Migrar Shopee/Telegram para esse contrato, preservando comportamento e testes.
- [ ] Documentar recuperação de reservas, concorrência e testes de reinício antes de adicionar outro canal.

## Fase 1 — WhatsApp

- [ ] Verificar elegibilidade e limites atuais da Groups API oficial para a conta e o tamanho de grupo desejado.
- [ ] Registrar com Marcos a escolha do canal e abordagem após essa verificação; nenhuma biblioteca não oficial foi escolhida.
- [ ] Se grupos oficiais não atenderem, avaliar alternativas (mensagens com consentimento ou divulgação manual) e impactos de outras integrações antes de implementá-las.
- [ ] Implementar publicador sobre o contrato comum, com controle de ritmo por canal.
- [ ] Criar testes mockados e executar piloto autorizado.

**Saída da fase:** abordagem decidida, integração testada e piloto validado. Não considerar “aquecimento” de número uma garantia de conformidade ou proteção contra bloqueios.

## Fase 2 — Instagram como divulgação

- [ ] Escolher Instagram Login ou Facebook Login e validar requisitos da conta profissional para a opção escolhida.
- [ ] Selecionar ofertas já publicadas em rotina de curadoria separada da fila imediata.
- [ ] Gerar imagem/carrossel com identidade visual e CTA para os grupos/link na bio.
- [ ] Implementar publicação oficial e testes mockados.
- [ ] Validar piloto com frequência definida e acompanhamento dos resultados.

## Próxima ação

Revisar e integrar o PR deste lote; depois executar o Lote 2, começando pela origem de preço/cupom e legenda. A integração do PR não instala nem reinicia os bots no computador de execução. WhatsApp e Instagram permanecem pendentes.
