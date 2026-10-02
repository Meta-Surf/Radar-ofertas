# Como contribuir

1. Crie uma branch para cada correção ou funcionalidade.
2. Configure um ambiente virtual e instale `requirements.txt` conforme o README.
3. Mantenha credenciais no `.env` local. Use valores fictícios em exemplos e testes.
4. Execute `python -m unittest discover -p "test_*.py" -v` na raiz.
5. Abra um pull request descrevendo o problema, a alteração e como foi validada.

Os testes automatizados não devem publicar no Telegram nem depender de credenciais reais. Testes manuais que acessam serviços externos ficam fora da suíte automática; o antigo teste real do Mercado Livre está arquivado em `archive/mercadolivre/teste_mercadolivre_real.py`.

Mantenha os comandos da raiz compatíveis com o fluxo Windows existente. Atualize `docs/operacao.md` e `.env.example`/`.env.exemplo` quando mudar configurações. Novas funcionalidades devem ser implementadas nos scripts atuais, não no arquivo histórico.

Nunca inclua `.env`, sessões do Telegram, tokens, filas, bancos ou respostas autenticadas em commits e relatos de erro. Revise `git diff --cached` antes de enviar alterações.
