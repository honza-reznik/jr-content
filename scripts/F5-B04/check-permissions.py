"""Živé ověření F5-B04 v Dockeru jrc-f5b04; nevypisuje tokeny ani hesla.

Spuštění: JRC_PROJECT=jrc-f5b04 docker compose --project-name jrc-f5b04
  exec -T jrc-bb-web python - < scripts/F5-B04/check-permissions.py
"""

import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urljoin, parse_qs

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.jrc")
import django

django.setup()

import requests
from django.conf import settings
from django.test import override_settings
from background_task.models import Task

from apps.accounts.models import User
from apps.jrc_video.client import FreeFrameClient
from apps.jrc_video.models import VideoAccountRevocation, VideoProject
from apps.jrc_video.services import sync_workspace
from apps.jrc_video.tickets import issue_ticket
from apps.members.models import WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

assert os.environ.get("APP_URL") == "http://127.0.0.1:13243", "Pouze projekt jrc-f5b04 se zadaným portem"


def call(method, path, *, token=None, body=None):
    return requests.request(
        method, settings.JRC_FF_API_URL + path, json=body,
        headers={"Authorization": f"Bearer {token}"} if token else {},
        timeout=10, proxies={"http": "", "https": ""}, allow_redirects=False,
    )


def token_for(user):
    response = call("POST", "/auth/jrc-exchange", body={"ticket": issue_ticket(user, audience="api")})
    assert response.status_code == 200, response.status_code
    return response.json()["access_token"]


def fetch_media(url, *, method="GET", data=None):
    # Preserve the original signed Host while reaching the same project's S3
    # gate from inside Docker. No request leaves the project's internal network.
    parsed = urlsplit(url)
    headers = {}
    if parsed.hostname == "127.0.0.1":
        assert parsed.port == 13963
        headers["Host"] = parsed.netloc
        target = f"http://jrc-ff-gate:13900{parsed.path}?{parsed.query}"
    else:
        target = urljoin(settings.JRC_FF_API_URL, url)
        assert urlsplit(target).hostname == "jrc-ff-api"
    return requests.request(method, target, headers=headers, data=data, timeout=20,
                            proxies={"http": "", "https": ""}, allow_redirects=False)


with override_settings(JRC_FF_SYNC_ENABLED=False):
    user = User.objects.create_user(email=f"f5b04-{uuid.uuid4().hex}@example.com", name="F5-B04 Demo")
    org = Organization.objects.create(name="F5-B04 živé ověření")
    workspace = Workspace.objects.create(organization=org, name="F5-B04 testovací klient")
    member = WorkspaceMembership.objects.create(workspace=workspace, user=user, workspace_role="editor")
service = FreeFrameClient()
sync_workspace(workspace.pk, api=service)
project_id = VideoProject.objects.get(workspace=workspace).ff_project_id
token = token_for(user)
assert call("GET", "/auth/me", token=token).json()["jrc_managed"] is True
for route in ["/auth/login", "/auth/send-magic-code", "/auth/verify-magic-code"]:
    response = call("POST", route, body={"email": user.email, "password": "password123", "code": "123456"})
    assert response.status_code == 403, (route, response.status_code)
assert call("POST", "/projects", token=token, body={"name": "Forbidden"}).status_code == 403
assert call("POST", "/auth/set-password", token=token, body={"password": "password123"}).status_code == 403
for action in ["approve", "reject"]:
    assert call("POST", f"/assets/{uuid.uuid4()}/{action}", token=token,
                body={"version_id": str(uuid.uuid4())}).status_code == 403
print("OK živé API: řízený účet jen vstupenkou, zákaz projektu, hesla a schvalování")

# A small generated video verifies real HLS manifests, proxy segment redirects
# and Garage signatures, before revoking this same playback session below.
with tempfile.TemporaryDirectory(prefix="f5b04-") as directory:
    video = Path(directory) / "probe.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=1920x1080:rate=25", "-f", "lavfi", "-i",
                    "sine=frequency=440:sample_rate=48000", "-t", "3", "-c:v", "libx264", "-threads", "2",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)], check=True)
    response = call("POST", "/upload/initiate", token=token, body={
        "project_id": str(project_id), "asset_name": "F5-B04 playback probe",
        "original_filename": "probe.mp4", "mime_type": "video/mp4", "file_size_bytes": video.stat().st_size,
    })
    assert response.status_code == 200, response.status_code
    upload = response.json()
    signed = call("POST", "/upload/presign-part", token=token, body={
        "s3_key": upload["s3_key"], "upload_id": upload["upload_id"], "part_number": 1,
    }).json()["presigned_url"]
    stored = fetch_media(signed, method="PUT", data=video.read_bytes())
    assert stored.status_code == 200, stored.status_code
    completion = {key: upload[key] for key in ("upload_id", "s3_key", "asset_id", "version_id")}
    response = call("POST", "/upload/complete", token=token, body={
        **completion, "parts": [{"PartNumber": 1, "ETag": stored.headers["ETag"]}],
    })
    assert response.status_code == 200, response.status_code
deadline = time.monotonic() + 120
while True:
    versions = call("GET", f"/assets/{upload['asset_id']}/versions", token=token).json()
    state = next(version for version in versions if version["id"] == upload["version_id"])["processing_status"]
    if state == "ready":
        break
    assert state != "failed" and time.monotonic() < deadline, state
    time.sleep(1)
playback = call("GET", f"/assets/{upload['asset_id']}/stream", token=token).json()["url"]
master = fetch_media(playback)
assert master.status_code == 200
variant = next(line for line in master.text.splitlines() if line and not line.startswith("#"))
variant_url = urljoin(playback, variant)
playlist = fetch_media(variant_url)
assert playlist.status_code == 200
segment = next(line for line in playlist.text.splitlines() if line and not line.startswith("#"))
segment_url = urljoin(variant_url, segment)
redirect = fetch_media(segment_url)
assert redirect.status_code == 302, redirect.status_code
signed_segment = redirect.headers["Location"]
assert int(parse_qs(urlsplit(signed_segment).query)["X-Amz-Expires"][0]) <= 180
assert fetch_media(signed_segment).status_code == 200
print("OK živé HLS: lokální video, skutečný převod, seznamy, ověřený segment a S3 podpis do 180 s")

# The exact same still-valid token sees a new role and then no membership.
assert call("GET", f"/projects/{project_id}", token=token).json()["role"] == "editor"
member.workspace_role = "viewer"
member.save(update_fields=["workspace_role"])
assert call("GET", f"/projects/{project_id}", token=token).json()["role"] == "viewer"
assert call("POST", "/upload/initiate", token=token, body={
    "project_id": str(project_id), "asset_name": "Forbidden", "original_filename": "x.mp4",
    "mime_type": "video/mp4", "file_size_bytes": 128,
}).status_code == 403
member.delete()
assert call("GET", f"/projects/{project_id}", token=token).status_code == 403
assert call("GET", f"/assets/{upload['asset_id']}/stream", token=token).status_code == 403
assert fetch_media(playback).status_code == 403
assert fetch_media(segment_url).status_code == 403
assert call("GET", "/auth/me", token=token).status_code == 200
print("OK živé API: změna role a odebrání členství platí hned se stejným tokenem")

user.is_active = False
user.save(update_fields=["is_active"])
assert call("GET", "/auth/me", token=token).status_code == 401
user.is_active = True
user.save(update_fields=["is_active"])
assert call("GET", "/auth/me", token=token).status_code == 401
fresh_token = token_for(user)
assert call("GET", "/auth/me", token=fresh_token).status_code == 200
print("OK živé API: blokace odvolá token i po následném odblokování")

# Simulate the remote API being unavailable only for the deleting request.
# The worker keeps its real internal URL and later retries the durable marker.
user_id = user.pk
assert Task.objects.filter(verbose_name="jrc_retry_freeframe_sync", repeat=60).exists()
with override_settings(JRC_FF_API_URL="http://jrc-ff-api:1"):
    user.delete()
assert VideoAccountRevocation.objects.filter(source_user_id=user_id).exists()
deadline = time.monotonic() + 90
while VideoAccountRevocation.objects.filter(source_user_id=user_id).exists():
    assert time.monotonic() < deadline, "Worker nezopakoval deaktivaci do 90 s"
    time.sleep(1)
assert call("GET", "/auth/me", token=fresh_token).status_code == 401
print("OK živý worker: po smazání uživatele při výpadku sám deaktivoval osiřelý účet")
