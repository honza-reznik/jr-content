"""P2-B15: stav úložiště a řádků MediaFile ve FreeFrame (jen čtení); spouští se v jrc-ff-api."""
import json

from apps.api.database import SessionLocal
from apps.api.models.asset import Asset, AssetVersion, MediaFile
from apps.api.services.s3_service import list_keys

db = SessionLocal()
keys = [(k, s) for prefix in ("raw/", "processed/") for k, _lm, s in list_keys(prefix)]
rows = db.query(MediaFile, AssetVersion, Asset).join(AssetVersion, AssetVersion.id == MediaFile.version_id).join(
    Asset, Asset.id == AssetVersion.asset_id).all()
print(json.dumps({
    "objects": len(keys), "bytes": sum(s for _k, s in keys),
    "media": [{"asset": a.name, "v": v.version_number, "status": v.processing_status.value,
               "deleted": bool(a.deleted_at or v.deleted_at), "raw": m.s3_key_raw} for m, v, a in rows],
}, ensure_ascii=False, indent=1))
