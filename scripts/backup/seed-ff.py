"""Vymyšlené testovací video ve FreeFrame pro test záloh (F2-B09); běží v obrazu jrc-ff-api se sítí hostitele."""
import hashlib
import json
import os
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

origin = f"http://127.0.0.1:{os.environ['JRC_APP_PORT']}"
base = origin + "/video-api"
token = None


def request(method, path, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base + path, method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


token = request("POST", "/auth/login", {"email": os.environ["JRC_FF_SYNC_EMAIL"],
                                       "password": os.environ["JRC_FF_SYNC_PASSWORD"]})["access_token"]
project = request("POST", "/projects", {"name": "Zálohový test – video"})
video = Path(tempfile.mkdtemp()) / "zaloha.mp4"
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25",
                "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000", "-t", "2", "-c:v", "libx264",
                "-threads", "2", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)], check=True)
source_sha = hashlib.sha256(video.read_bytes()).hexdigest()
upload = request("POST", "/upload/initiate", {
    "project_id": project["id"], "asset_name": "Zálohové video", "original_filename": video.name,
    "mime_type": "video/mp4", "file_size_bytes": video.stat().st_size})
parts = []
with video.open("rb") as stream:
    while chunk := stream.read(upload["chunk_size_bytes"]):
        number = len(parts) + 1
        signed = request("POST", "/upload/presign-part", {
            "s3_key": upload["s3_key"], "upload_id": upload["upload_id"], "part_number": number})
        put = urllib.request.Request(signed["presigned_url"], data=chunk, method="PUT")
        with urllib.request.urlopen(put, timeout=60) as response:
            parts.append({"PartNumber": number, "ETag": response.headers["ETag"]})
request("POST", "/upload/complete", {**{k: upload[k] for k in ("upload_id", "s3_key", "asset_id", "version_id")}, "parts": parts})
started = time.monotonic()
state = None
while time.monotonic() - started < 180:
    versions = request("GET", f"/assets/{upload['asset_id']}/versions")
    state = next(v for v in versions if v["id"] == upload["version_id"])["processing_status"]
    if state in ("ready", "failed"):
        break
    time.sleep(2)
assert state == "ready", state
print(json.dumps({"project_id": project["id"], "asset_id": upload["asset_id"], "version_id": upload["version_id"],
                  "source_sha256": source_sha, "source_bytes": video.stat().st_size}))
