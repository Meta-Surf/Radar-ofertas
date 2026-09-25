"""Consulta MANUAL à API real. Nunca executar em CI; testes ficam em tests/."""
import os
import requests
from dotenv import load_dotenv


def main():
    # Defesa adicional: até uma chamada acidental no CI não acessa a rede.
    if os.getenv("CI", "").lower() not in ("", "0", "false"):
        raise SystemExit("Consulta real desativada em CI.")
    load_dotenv()
    token = os.getenv("ML_ACCESS_TOKEN")
    if not token:
        raise SystemExit("Preencha ML_ACCESS_TOKEN no .env para a consulta manual.")
    resposta = requests.get(
        "https://api.mercadolibre.com/users/me",
        headers={"Authorization": f"Bearer {token}"}, timeout=30,
    )
    print("Status:", resposta.status_code)
    print(resposta.json())


if __name__ == "__main__":
    main()
