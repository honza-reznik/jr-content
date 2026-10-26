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

# F2-B05: web i worker ukládají do soukromého bucketu jrc-app (vnitřní adresa Garage),
# adresy pro prohlížeč jsou krátkodobě podepsané pro místní bránu S3.
import io  # noqa: E402
import os  # noqa: E402
import uuid  # noqa: E402
from pathlib import Path  # noqa: E402
from urllib.parse import parse_qs, urlsplit  # noqa: E402

sys.path.insert(0, "/app")
import django  # noqa: E402

django.setup()
from django.core.files.base import ContentFile  # noqa: E402
from django.core.files.storage import default_storage  # noqa: E402
from django.core.management import call_command  # noqa: E402

from apps.media_library.storage import is_s3_backend  # noqa: E402

assert is_s3_backend() and default_storage.bucket_name == "jrc-app"
assert default_storage.connection.meta.client.meta.endpoint_url == "http://jrc-s3:3900"
payload = f"JR Content smoke {sys.argv[1]}\n".encode()
name = default_storage.save(f"jrc-smoke/{uuid.uuid4()}.txt", ContentFile(payload))
try:
    with default_storage.open(name, "rb") as stored:
        assert stored.read() == payload
    url = urlsplit(default_storage.url(name))
    assert f"{url.scheme}://{url.netloc}" == os.environ["JRC_S3_PUBLIC_URL"].rstrip("/")
    assert url.path == os.environ.get("JRC_S3_PUBLIC_PATH", "") + f"/jrc-app/{name}"
    assert parse_qs(url.query)["X-Amz-Expires"] == ["900"]
finally:
    default_storage.delete(name)
print(f"OK Brightbean {sys.argv[1]}: zápis a čtení v jrc-app, podepsaná adresa na místní bránu S3 (900 s)")

if sys.argv[1] == "web":
    # Migrace médií proběhla při make up (značka v bucketu); další běh už nic nekopíruje,
    # takže stará místní záloha nikdy nepřepíše ani neobnoví média v S3.
    from apps.jrc_storage.management.commands.jrc_migrate_media_to_s3 import MARKER_KEY  # noqa: E402

    assert default_storage.exists(MARKER_KEY), "Migrace médií do S3 není dokončená"
    local = Path(os.environ["MEDIA_ROOT"], "jrc-smoke", f"{uuid.uuid4()}.txt")
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(payload)
    key = local.relative_to(os.environ["MEDIA_ROOT"]).as_posix()
    try:
        out = io.StringIO()
        call_command("jrc_migrate_media_to_s3", stdout=out)
        assert "already completed; nothing copied" in out.getvalue(), out.getvalue()
        assert not default_storage.exists(key)
        assert local.read_bytes() == payload
    finally:
        local.unlink()
    print("OK Brightbean: migrace médií do jrc-app dokončená, další běh nic nekopíruje ani nepřepisuje")
