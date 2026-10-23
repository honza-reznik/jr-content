"""Živé ověření Brightbean; spouští se v kontejnerech webu a workeru."""

import json
import socket
import subprocess
import sys
import urllib.request

# Zdrojový bind mount nestačí: Node převodník a jeho závislosti musí být v obrazu.
markdown = "Scénář **test**"
conversion = subprocess.run(
    ["node", "/opt/jrc-texts/bridge.mjs"],
    input=json.dumps({"markdown": markdown}),
    text=True,
    stdout=subprocess.PIPE,
    check=True,
    timeout=10,
)
converted = json.loads(conversion.stdout)
assert converted["text"] == markdown
assert converted["content"]["type"] == "doc"
print("OK Brightbean: serverový převodník scénáře je dostupný v obrazu a převádí Markdown")

if sys.argv[1] == "web":
    with urllib.request.urlopen("http://127.0.0.1:8000/health/", timeout=10) as response:
        assert response.status == 200
        assert json.load(response) == {"status": "ok"}
    for endpoint in ("/accounts/login/", "/static/css/dist/styles.css"):
        with urllib.request.urlopen(f"http://127.0.0.1:8000{endpoint}", timeout=10) as response:
            assert response.status == 200
            assert response.read()
    print("OK Brightbean: /health/, přihlášení a sestavené CSS")
elif sys.argv[1] == "worker":
    # Číselná adresa ověří skutečné směrování, nikoli pouhé selhání DNS.
    try:
        connection = socket.create_connection(("1.1.1.1", 443), timeout=3)
    except OSError:
        print("OK worker: spojení na 1.1.1.1:443 je zablokované")
    else:
        connection.close()
        raise AssertionError("Worker má přístup k internetu")
else:
    raise ValueError("Neznámý režim smoke testu")
