# Publicação automática a partir do link Mercado Livre

> Guia funcional atual. Para a sequência completa de inicialização, use [docs/operacao.md](docs/operacao.md).

No grupo -1003988174916, o operador pode enviar somente seu link de produto. O
monitor coloca a mensagem na fila; o publicador busca título, preço público e
imagem, prepara a mensagem e envia com o endereço de afiliado original.

## Instalar no Windows

1. Faça backup da pasta BOPT e feche monitor e publicador.
2. Extraia este pacote na pasta BOPT, mantendo as subpastas e substituindo o código.
3. Execute instalar_ml_automatico.cmd uma vez. Ele instala as dependências Python
   e um Chromium isolado para a leitura das páginas. Não altera o .env.
4. Reinicie monitor e publicador pelos comandos que já utiliza.
5. Envie novamente o link no grupo Publicador Mercado Livre. Pode ser só a URL.

Alternativa ao instalador, no PowerShell dentro de BOPT:

    py -m pip install -r requirements.txt
    py -m playwright install chromium

Mensagens recentes que já estiverem na fila podem ser completadas automaticamente.
Mensagens antes rejeitadas e fora da janela de recuperação precisam ser enviadas novamente ou
editadas. Ao reiniciar, o monitor pode reprocessar mensagens recentes conforme TG_RECUPERAR_MINUTOS; não há varredura ilimitada do histórico.

## Comportamento

- O endereço de afiliado original permanece no botão; não gera outro link.
- Primeiro tenta ler a página por HTTP. Se necessário, tenta um navegador com
  JavaScript. Não usa sua sessão pessoal nem automatiza login, captcha ou compra.
- A consulta roda em segundo plano: os demais grupos continuam publicando.
- Usa um único produto e uma única oferta explícita em BRL, disponível para compra,
  com imagem ML. Não usa parcela, preço riscado, lowPrice ou estimativa de cupom.
- O preço extraído é identificado como preço público informado na página. Pode
  diferir de condições personalizadas no carrinho; não afirma preço final da conta.
- Dados já escritos pelo operador continuam prevalecendo; completa campos ausentes.
- Se a página for uma vitrine, só segue um destino de produto inequívoco. Não escolhe
  entre vários produtos. Faixa de preços, bloqueio ou dados ausentes ficam pendentes.
- Reconsulta após cinco minutos, dentro da idade máxima da fila. Mantém proteção
  contra repetição e contra reenvio após resultado incerto do Telegram.
- Listas de cupons com códigos continuam no fluxo existente. Um link de página de
  cupons sem códigos não permite inventar a lista de descontos.

## Diagnóstico opcional, sem publicar

    py mercadolivre_auto.py --testar "https://meli.la/2wpg8CQ"

Mostra produto, preço e presença de imagem quando obtidos, ou o motivo do bloqueio.
Não grava fila e não envia ao Telegram. Esse teste não é necessário a cada oferta.

## Validação e limites desta entrega

158 testes locais aprovados com dados sintéticos e rede simulada, incluindo o
encadeamento captura, complementação, publicação, preservação de links e execução
em segundo plano. Nenhuma publicação real foi feita.

O link 2wpg8CQ retornou HTTP 403 neste ambiente. O download do navegador de teste
retornou um arquivo inválido, impedindo validar a navegação real aqui. Portanto,
não foi confirmado o destino, preço nem a imagem reais da câmera Intelbras. O
resultado desse link ainda precisa ser verificado na rede do computador Windows.
A automação não garante acesso a páginas que o Mercado Livre bloqueie também ali.
