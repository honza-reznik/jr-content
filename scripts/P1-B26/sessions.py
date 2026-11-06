"""Přihlášení pouze vymyšlených účtů ve vlastním projektu pro testy rozvržení.

Spouštět přes manage.py shell; vstup i výstup obsahují tajné údaje a patří jen
do ignorovaných souborů s právy 600. Zabraňuje opakování přihlášení v každém
výřezu; testování přihlašování není součástí tohoto bloku.
"""
import json
import os
from importlib import import_module
from pathlib import Path

from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY

from apps.accounts.models import User

assert os.environ.get("JRC_PROJECT") == "jrc-p1b26"
path = Path("/tmp/jrc-p1b26-fixture.json")
fixture = json.loads(path.read_text())
for role in ("admin", "client"):
    user = User.objects.get(email=fixture[role]["email"])
    assert user.email.endswith("@f10-b02.example.invalid")
    session = import_module(settings.SESSION_ENGINE).SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = settings.AUTHENTICATION_BACKENDS[0]
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.save()
    fixture[role]["cookie"] = {"name": settings.SESSION_COOKIE_NAME, "value": session.session_key}
path.write_text(json.dumps(fixture))
