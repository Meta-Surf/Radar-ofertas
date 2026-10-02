import os
import requests
from dotenv import load_dotenv

load_dotenv()

ACCESS_TOKEN = os.getenv("ML_ACCESS_TOKEN")

url = "https://api.mercadolibre.com/users/me"

headers = {
    "Authorization": f"Bearer {ACCESS_TOKEN}"
}

resposta = requests.get(url, headers=headers, timeout=30)

print("Status:", resposta.status_code)
print(resposta.json())