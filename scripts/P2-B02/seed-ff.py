"""Krátké lokální video a veřejný/chráněný odkaz; jen vlastní Docker projekt."""
import json
import os
import secrets
import subprocess
from pathlib import Path
from uuid import UUID

from apps.api.config import settings
from apps.api.database import SessionLocal
from apps.api.models.asset import Asset, AssetVersion, MediaFile
from apps.api.models.share import ShareLink
from apps.api.models.project import Project
from apps.api.services.auth_service import hash_password
from apps.api.services.s3_service import get_s3_client

assert os.environ.get("JRC_PROJECT") == "jrc-p2b02"
data = json.loads(Path("/tmp/p2b02-fixture.json").read_text())
folder = Path("/tmp/p2b02-video")
folder.mkdir(exist_ok=True)
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25",
                "-t", "2", "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p", str(folder / "original.mp4")], check=True, timeout=30)
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(folder / "original.mp4"), "-c", "copy",
                "-f", "hls", "-hls_time", "1", "-hls_segment_filename", str(folder / "seg%d.ts"), str(folder / "stream.m3u8")], check=True, timeout=30)
(folder / "master.m3u8").write_text('#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=500000,RESOLUTION=640x360\nstream.m3u8\n')
s3 = get_s3_client()
prefix = "processed/p2b02"
for file in folder.iterdir():
    s3.upload_file(str(file), settings.s3_bucket, prefix + "/" + file.name)
with SessionLocal() as db:
    asset = Asset(project_id=UUID(data["project"]), created_by=UUID(data["owner_id"]), asset_type="video", name="P2-B02 video — long name for a clear return destination and mobile layout")
    db.add(asset)
    db.flush()
    version = AssetVersion(asset_id=asset.id, version_number=1, processing_status="ready", created_by=UUID(data["owner_id"]))
    db.add(version)
    db.flush()
    db.add(MediaFile(version_id=version.id, file_type="video", original_filename="p2b02.mp4", mime_type="video/mp4", file_size_bytes=(folder / "original.mp4").stat().st_size,
                     s3_key_raw=prefix + "/original.mp4", s3_key_processed=prefix, width=640, height=360, duration_seconds=2, fps=25))
    links = {}
    # JRC shares require membership even when marked public; standalone shares
    # retain their anonymous/password behavior. Exercise both existing policies.
    managed_token = secrets.token_urlsafe(24)
    db.add(ShareLink(asset_id=asset.id, token=managed_token, created_by=UUID(data["owner_id"]), permission="view"))
    project = Project(name="P2-B02 standalone share", created_by=UUID(data["owner_id"]), jrc_managed=False)
    db.add(project)
    db.flush()
    shared_asset = Asset(project_id=project.id, created_by=UUID(data["owner_id"]), asset_type="video", name="P2-B02 shared video")
    db.add(shared_asset)
    db.flush()
    shared_version = AssetVersion(asset_id=shared_asset.id, version_number=1, processing_status="ready", created_by=UUID(data["owner_id"]))
    db.add(shared_version)
    db.flush()
    db.add(MediaFile(version_id=shared_version.id, file_type="video", original_filename="share.mp4", mime_type="video/mp4",
                     file_size_bytes=(folder / "original.mp4").stat().st_size, s3_key_raw=prefix + "/original.mp4", s3_key_processed=prefix,
                     width=640, height=360, duration_seconds=2, fps=25))
    for protected in (False, True):
        token = secrets.token_urlsafe(24)
        db.add(ShareLink(asset_id=shared_asset.id, token=token, created_by=UUID(data["owner_id"]), permission="view",
                         password_hash=hash_password("p2b02-share-test") if protected else None))
        links["protected" if protected else "public"] = token
    db.commit()
    print(json.dumps({"asset": str(asset.id), "share": links, "managed_share": managed_token, "hls_prefix": prefix}))
