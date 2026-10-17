"""Troca o código de autorização da Nuvemshop pelo token permanente do emissor.

Uso (no Terminal, dentro desta pasta):  python obter_token.py
Pede o ID do app, o segredo do app e o código que aparece na URL depois de instalar o app na loja.
Mostra o token para você colar no segredo NUVEMSHOP_TOKEN do GitHub. Nada é salvo em arquivo.
"""
import getpass
import json
import urllib.request

client_id = input("ID do app (client_id): ").strip()
client_secret = getpass.getpass("Segredo do app (client_secret, não aparece ao digitar): ").strip()
code = input("Código da URL (o valor depois de code=): ").strip()

body = json.dumps({"client_id": client_id, "client_secret": client_secret,
                   "grant_type": "authorization_code", "code": code}).encode()
req = urllib.request.Request("https://www.tiendanube.com/apps/authorize/token", data=body,
                             headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode())
except Exception as e:
    print("\nNão deu certo:", e)
    print("O código da URL vale por poucos minutos e só uma vez. Reinstale o app e tente de novo.")
    raise SystemExit(1)

if "access_token" not in data:
    print("\nResposta inesperada:", data)
    raise SystemExit(1)
print("\nID da loja:", data.get("user_id"))
print("TOKEN (copie tudo e cole no segredo NUVEMSHOP_TOKEN do GitHub):\n")
print(data["access_token"])
