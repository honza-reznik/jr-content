"""Measure only synthetic data in the isolated P2-B05 project, inside Docker."""
import json
import os
from apps.api.database import SessionLocal
from apps.api.models.asset import Asset, MediaFile, AssetVersion
from apps.api.services.s3_service import get_s3_client
from apps.api.config import settings

assert settings.frontend_url.startswith("http://127.0.0.1:13905/")
with SessionLocal() as db:
    media = db.query(MediaFile).filter(MediaFile.original_filename == "Žlutý klip – original.MOV").order_by(MediaFile.created_at.desc()).first()
    assert media and media.s3_key_processed
    client = get_s3_client()
    root = media.s3_key_processed
    master = client.get_object(Bucket=settings.s3_bucket, Key=f"{root}/master.m3u8")["Body"].read().decode()
    result = {"master": master, "renditions": []}
    for line in master.splitlines():
        if not line or line.startswith("#"):
            continue
        directory = line.rsplit("/", 1)[0]
        playlist = client.get_object(Bucket=settings.s3_bucket, Key=f"{root}/{line}")["Body"].read().decode()
        durations, sizes = [], []
        for entry in playlist.splitlines():
            if entry.startswith("#EXTINF:"):
                durations.append(float(entry.split(":")[1].split(",")[0]))
            elif entry and not entry.startswith("#"):
                sizes.append(client.head_object(Bucket=settings.s3_bucket, Key=f"{root}/{directory}/{entry}")["ContentLength"])
        result["renditions"].append({"playlist": line, "duration_s": sum(durations), "segment_bytes": sizes,
                                     "average_bps_including_audio_and_ts": round(sum(sizes) * 8 / sum(durations))})
    print(json.dumps(result, indent=2))
