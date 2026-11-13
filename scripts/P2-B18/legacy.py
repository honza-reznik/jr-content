"""Vlastní starší ready verze bez HLS; stav se po ověření obnoví."""
import json
import sys
from pathlib import Path
from apps.api.database import SessionLocal
from apps.api.models.asset import MediaFile
from apps.api.config import settings
assert ':13918/' in settings.frontend_url
f = json.loads(input())
version = next(v for v in f['versions'] if v['number'] == 2)
backup = Path('/tmp/p2b18-hls.json')
with SessionLocal() as db:
    media = db.query(MediaFile).filter(MediaFile.version_id == version['ff']).one()
    if sys.argv[1] == 'clear':
        backup.write_text(json.dumps({'processed': media.s3_key_processed}))
        media.s3_key_processed = None
    else:
        media.s3_key_processed = json.loads(backup.read_text())['processed']
        backup.unlink()
    db.commit()
print('OK ověřovací stav HLS změněn')
