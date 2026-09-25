import os
import base64
import hashlib
import secrets

import requests
from flask import Flask, request
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

CLIENT_ID = os.getenv("ML_CLIENT_ID")
CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET")
REDIRECT_URI = os.getenv("ML_REDIRECT_URI")

AUTH_URL = "https://auth.mercadolivre.com.br/authorization"
TOKEN_URL = "https://api.mercadolibre.com/oauth/token"

code_verifier = base64.urlsafe_b64encode(
    secrets.token_bytes(32)
).decode().rstrip("=")

code_challenge = base64.urlsafe_b64encode(
    hashlib.sha256(code_verifier.encode()).digest()
).decode().rstrip("=")

state = secrets.token_urlsafe(24)


@app.route("/")
def inicio():
    url = (
        f"{AUTH_URL}"
        f"?response_type=code"
        f"&client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&state={state}"
        f"&code_challenge={code_challenge}"
        f"&code_challenge_method=S256"
    )

    return f"""
    <h2>Radar de Ofertas</h2>
    <p>Conecte sua conta do Mercado Livre:</p>
    <a href="{url}">Conectar Mercado Livre</a>
    """


@app.route("/callback")
def callback():
    codigo = request.args.get("code")
    estado_recebido = request.args.get("state")

    if estado_recebido != state:
        return "Erro: state invalido."

    if not codigo:
        return "Codigo de autorizacao nao recebido."

    dados = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": codigo,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": code_verifier
    }

    resposta = requests.post(
        TOKEN_URL,
        data=dados,
        timeout=30
    )

    if resposta.status_code != 200:
        return f"""
        <h3>Erro ao gerar token</h3>
        <pre>{resposta.text}</pre>
        """

    token = resposta.json()

    print("\nACCESS TOKEN:")
    print(token.get("access_token"))

    print("\nREFRESH TOKEN:")
    print(token.get("refresh_token"))

    return """
    <h2>Mercado Livre conectado com sucesso!</h2>
    <p>O token foi gerado.</p>
    <p>Volte ao terminal.</p>
    """


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)