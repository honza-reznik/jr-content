import json
from apps.api.database import SessionLocal
from apps.api.models.asset import AssetVersion, MediaFile
from apps.api.config import settings
assert ':13918/' in settings.frontend_url
f = json.loads(input())
with SessionLocal() as db:
    media = db.query(MediaFile).join(AssetVersion).filter(AssetVersion.asset_id == f['asset']).all()
    f['keys'] = [key for m in media for key in [m.s3_key_raw, m.s3_key_download] if key]
print(json.dumps(f))
