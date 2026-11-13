"""Temporarily simulate a missing original, only in our own synthetic fixture."""
import json
import os
from pathlib import Path
from apps.api.database import SessionLocal
from apps.api.models.asset import MediaFile
assert os.environ['JRC_PROJECT'] == 'jrc-p2b05'
fixture = json.loads(input())
backup = Path('/tmp/p2b05-original-key.json')
with SessionLocal() as db:
    media = db.query(MediaFile).filter(MediaFile.version_id == fixture['version']).one()
    if os.environ['P2B05_FALLBACK'] == '1':
        assert not backup.exists()
        backup.write_text(json.dumps({'version': fixture['version'], 'key': media.s3_key_raw}))
        media.s3_key_raw = 'raw/p2b05-missing-original.mov'
    else:
        saved = json.loads(backup.read_text())
        assert saved['version'] == fixture['version']
        media.s3_key_raw = saved['key']
    db.commit()
    if os.environ['P2B05_FALLBACK'] != '1':
        backup.unlink()
print('OK synthetic original pointer changed')
