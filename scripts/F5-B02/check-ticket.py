"""Živé ověření vstupenky Brightbean → FreeFrame (F5-B02); spouští check-ticket.sh v Dockeru.

Používá jen vymyšlené účty v projektu jrc-f5b02. Vstupenky ani tokeny nevypisuje.
"""

import os
import time
import uuid

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.jrc")
import django

django.setup()

import requests
from django.conf import settings
from django.test import override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.jrc_video.client import _USER_TOKENS, FreeFrameClient, FreeFrameError, FreeFrameUserClient
from apps.jrc_video.models import VideoAccount, VideoProject
from apps.jrc_video.services import sync_account, sync_workspace
from apps.jrc_video.tickets import encode_ticket, issue_ticket
from apps.members.models import WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

# Stejný jmenný prostor jako freeframe/apps/api/routers/jrc_sync.py.
NAMESPACE = uuid.UUID("29c4ee9e-ab19-48c4-bb44-2fc77109b234")
DIRECT = settings.JRC_FF_API_URL  # http://jrc-ff-api:8000
PUBLIC = "http://jrc-proxy:8080/video-api"
CLIENT_ID = uuid.UUID("f5b02000-0000-4000-8000-000000000101")
EDITOR_ID = uuid.UUID("f5b02000-0000-4000-8000-000000000102")
WORKSPACE_ID = uuid.UUID("f5b02000-0000-4000-8000-000000000001")
NO_PROXY = {"http": "", "https": ""}


def exchange(ticket, base=DIRECT):
    return requests.post(f"{base}/auth/jrc-exchange", json={"ticket": ticket}, timeout=10, proxies=NO_PROXY)


def me(token, base=DIRECT):
    return requests.get(f"{base}/auth/me", headers={"Authorization": f"Bearer {token}"}, timeout=10, proxies=NO_PROXY)


def claims_for(user_id, *, aud="api", now=None, lifetime=60, sub=None):
    issued = int(time.time() if now is None else now)
    return {
        "v": 1,
        "iss": "jrc-brightbean",
        "aud": aud,
        "acct": str(user_id),
        "sub": str(sub or uuid.uuid5(NAMESPACE, f"account:{user_id}")),
        "iat": issued,
        "exp": issued + lifetime,
        "nonce": uuid.uuid4().hex,
    }


def rejected(response, label):
    assert response.status_code == 401, (label, response.status_code)
    assert response.json() == {"detail": "Invalid or expired ticket"}, label
    print(f"OK odmítnuto: {label}")


secret = settings.JRC_FF_SSO_SECRET.encode()
assert len(secret) >= 32, "JRC_FF_SSO_SECRET chybí"
service = FreeFrameClient()

# Příprava: klientka (reviewer) a editor ve stejném klientském prostoru; idempotentní.
org, _ = Organization.objects.get_or_create(name="F5-B02 live verification")
workspace = Workspace.objects.filter(pk=WORKSPACE_ID).first() or Workspace.objects.create(
    id=WORKSPACE_ID, organization=org, name="F5-B02 client"
)
users = {}
for identity, role in [(CLIENT_ID, "client"), (EDITOR_ID, "editor")]:
    user = User.objects.filter(pk=identity).first()
    if user is None:
        with override_settings(JRC_FF_SYNC_ENABLED=False):
            user = User.objects.create_user(id=identity, email=f"f5b02-{role}@example.invalid", name=f"F5-B02 {role}")
            Workspace.objects.filter(memberships__user=user).delete()
    if not user.is_active:
        user.is_active = True
        user.save(update_fields=["is_active"])
    WorkspaceMembership.objects.get_or_create(workspace=workspace, user=user, defaults={"workspace_role": role})
    users[role] = user
sync_workspace(workspace.pk, api=service)
client_user, editor = users["client"], users["editor"]
client_account = VideoAccount.objects.get(user=client_user)
editor_account = VideoAccount.objects.get(user=editor)
project_id = VideoProject.objects.get(workspace=workspace).ff_project_id
_USER_TOKENS.clear()

# AK3: server Brightbean volá API jménem uživatele přímo na jrc-ff-api:8000.
assert DIRECT == "http://jrc-ff-api:8000"
remote_me = FreeFrameUserClient(client_user).request("GET", "/auth/me")
assert remote_me["id"] == str(client_account.ff_user_id) and remote_me["email"] == client_user.email
project = FreeFrameUserClient(client_user).request("GET", f"/projects/{project_id}")
assert project["role"] == "reviewer", project["role"]
assert FreeFrameUserClient(editor).request("GET", f"/projects/{project_id}")["role"] == "editor"
# FreeFrame uplatní roli skutečného uživatele, ne servisního účtu: klientka nesmí nahrávat.
try:
    FreeFrameUserClient(client_user).request("POST", "/upload/initiate", {
        "project_id": str(project_id), "asset_name": "Forbidden", "original_filename": "x.mp4",
        "mime_type": "video/mp4", "file_size_bytes": 128,
    })
except FreeFrameError as exc:
    assert exc.status_code == 403, exc.status_code
else:
    raise AssertionError("reviewer could upload")
# Servisní cesty nejsou dostupné jménem uživatele.
try:
    FreeFrameUserClient(client_user).request("PUT", f"/jrc-sync/projects/{uuid.uuid4()}", {"name": "x"})
except FreeFrameError as exc:
    assert exc.status_code == 403, exc.status_code
else:
    raise AssertionError("user reached /jrc-sync")
print("OK volání jménem uživatele: /auth/me, role reviewer/editor, zákaz nahrávání a /jrc-sync")

# Platná vstupenka a dvojí použití.
ticket = issue_ticket(client_user, audience="api")
first = exchange(ticket)
assert first.status_code == 200, first.status_code
assert first.headers["cache-control"] == "no-store"
assert set(first.json()) == {"access_token", "token_type", "expires_in"}
assert me(first.json()["access_token"]).json()["id"] == str(client_account.ff_user_id)
rejected(exchange(ticket), "dvojí použití")

# Souběh: stejná vstupenka desetkrát najednou, uspěje nejvýš jedna.
from concurrent.futures import ThreadPoolExecutor

ticket = issue_ticket(client_user, audience="api")
with ThreadPoolExecutor(10) as pool:
    codes = sorted(pool.map(lambda _: exchange(ticket).status_code, range(10)))
assert codes == [200] + [401] * 9, codes
print("OK souběžné použití: 1× 200, 9× 401")

rejected(exchange(encode_ticket(claims_for(CLIENT_ID, now=time.time() - 61), secret)), "prošlá")
rejected(exchange(encode_ticket(claims_for(CLIENT_ID, lifetime=600), secret)), "platnost delší než 60 s")
rejected(exchange(encode_ticket(claims_for(CLIENT_ID), b"x" * 64)), "špatný podpis")
rejected(exchange(encode_ticket(claims_for(CLIENT_ID), settings.JRC_FF_SYNC_PASSWORD.encode())), "podpis heslem")
tampered = issue_ticket(client_user, audience="api").split(".")
other = encode_ticket(claims_for(EDITOR_ID), secret).split(".")
rejected(exchange(".".join([tampered[0], other[1], tampered[2]])), "vyměněný obsah se starým podpisem")
rejected(exchange(encode_ticket(claims_for(CLIENT_ID, sub=editor_account.ff_user_id), secret)), "cizí účet")
rejected(exchange(encode_ticket(claims_for(uuid.uuid4()), secret)), "neexistující účet")
service_id = next(m["user_id"] for m in service.request("GET", f"/projects/{project_id}/members") if m["role"] == "owner")
rejected(exchange(encode_ticket(claims_for(CLIENT_ID, sub=service_id), secret)), "servisní superadmin")
rejected(exchange(issue_ticket(client_user, audience="api"), base=PUBLIC), "serverová vstupenka přes veřejný vstup")

# Review F5-B02: chybný tvar těla nesmí vrátit vstupenku zpět (FastAPI 422 by ji zopakoval).
for base in (DIRECT, PUBLIC):
    ticket = issue_ticket(client_user, audience="web")
    for body in ({"ticket": [ticket]}, {"ticket": {"t": ticket}}, [ticket], {"ticket": ticket, "x": 1}):
        bad = requests.post(f"{base}/auth/jrc-exchange", json=body, timeout=10, proxies=NO_PROXY)
        assert ticket not in bad.text and ticket.split(".")[2] not in bad.text, base
        assert bad.headers["cache-control"] == "no-store", base
        rejected(bad, f"chybný tvar těla bez ozvěny ({'vnitřní' if base == DIRECT else 'veřejný'} vstup)")
    assert exchange(ticket, base=base).status_code == 200

# Prohlížeč (podklad F5-B03): webová vstupenka přes společný vstup /video-api.
web = exchange(issue_ticket(client_user, audience="web", next_path=f"/projects/{project_id}"), base=PUBLIC)
assert web.status_code == 200, web.status_code
body = web.json()
assert body["next"] == f"/projects/{project_id}" and body["refresh_token"]
assert me(body["access_token"], base=PUBLIC).json()["id"] == str(client_account.ff_user_id)
print("OK webová vstupenka přes /video-api: tokeny, next, /auth/me")

# Vypnutý účet: Brightbean vstupenku nevydá a FreeFrame ji odmítne i podepsanou ručně.
client_user.is_active = False
client_user.save(update_fields=["is_active"])  # signál synchronizace vypne i účet FreeFrame
try:
    FreeFrameUserClient(client_user).request("GET", "/auth/me")
except FreeFrameError:
    pass
else:
    raise AssertionError("blocked user call")
rejected(exchange(encode_ticket(claims_for(CLIENT_ID), secret)), "vypnutý účet")
remote = next(u for u in service.request("GET", "/admin/users") if u["id"] == str(client_account.ff_user_id))
assert remote["status"] == "deactivated", remote["status"]
client_user.is_active = True
client_user.save(update_fields=["is_active"])
assert FreeFrameUserClient(client_user).request("GET", "/auth/me")["id"] == str(client_account.ff_user_id)
print("OK vypnutý účet odmítnut, po odblokování opět funguje")
sync_account(client_user.pk, service)
print(f"OK F5-B02 živé ověření ({timezone.now():%Y-%m-%d %H:%M})")
