"""Dočasně odstraní jediný syntetický originál a zase jej obnoví; jen jrc-p2b18."""
import hashlib
import json
import sys
from pathlib import Path
from apps.api.database import SessionLocal
from apps.api.models.asset import MediaFile
from apps.api.services.s3_service import get_s3_client
from apps.api.config import settings
assert ':13918/' in settings.frontend_url
fixture = json.loads(input())
version = next(v for v in fixture['versions'] if v['number'] == 2)
with SessionLocal() as db:
    media = db.query(MediaFile).filter(MediaFile.version_id == version['ff']).one()
    s3 = get_s3_client()
    backup = Path('/tmp/p2b18-original.mov')
    if sys.argv[1] == 'remove':
        s3.download_file(settings.s3_bucket, media.s3_key_raw, str(backup))
        s3.delete_object(Bucket=settings.s3_bucket, Key=media.s3_key_raw)
        body = s3.get_object(Bucket=settings.s3_bucket, Key=media.s3_key_download)['Body']
        try:
            print(json.dumps({'sha256': hashlib.sha256(body.read()).hexdigest()}))
        finally:
            body.close()
    else:
        s3.upload_file(str(backup), settings.s3_bucket, media.s3_key_raw, ExtraArgs={'ContentType': 'video/quicktime'})
        backup.unlink()
        print('OK obnoven vlastní syntetický originál')
