"""Kontrola FreeFrame po obnově (F2-B09): přihlášení, video je ready, přehrávací seznam HLS se načte."""
import json
import os
import sys
import urllib.request

origin = f"http://127.0.0.1:{os.environ['JRC_APP_PORT']}"
base = origin + "/video-api"
seed = json.load(open(sys.argv[1]))
token = None


def request(method, path, body=None, raw=False):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(path if raw else base + path, method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=30) as response:
        data = response.read()
        return data if raw else (json.loads(data) if data else None)


token = request("POST", "/auth/login", {"email": os.environ["JRC_FF_SYNC_EMAIL"],
                                       "password": os.environ["JRC_FF_SYNC_PASSWORD"]})["access_token"]
projects = request("GET", "/projects")
names = [p["name"] for p in (projects if isinstance(projects, list) else projects.get("items", projects.get("projects", [])))]
assert "Zálohový test – video" in names, names
versions = request("GET", f"/assets/{seed['asset_id']}/versions")
version = next(v for v in versions if v["id"] == seed["version_id"])
assert version["processing_status"] == "ready", version["processing_status"]
stream = request("GET", f"/assets/{seed['asset_id']}/stream?version_id={seed['version_id']}")
playlist = request("GET", base + stream["url"], raw=True)
assert playlist.startswith(b"#EXTM3U"), playlist[:40]
print("OK FreeFrame po obnově: přihlášení, projekt, verze videa ready, HLS seznam se načítá")
