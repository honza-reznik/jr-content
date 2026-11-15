"""P2-B15: smazání zdrojové větve (asset YouTube · Video) a její okamžité trvalé odstranění; jen jrc-p2b15.

Asset se smaže jako v aplikaci (deleted_at), posune se za lhůtu koše a spustí se skutečný úklid
s mazáním v úložišti. Sdílené soubory musí zůstat, protože na ně ukazují výstupy Shorts a Story.
"""
import json
import os
from datetime import datetime, timedelta, timezone

from apps.api.database import SessionLocal
from apps.api.models.asset import Asset
from apps.api.tasks.cleanup_tasks import _run_cleanup

assert os.environ.get("JRC_PROJECT") == "jrc-p2b15"
db = SessionLocal()
source = db.query(Asset).filter(Asset.name.like("% · YouTube · Video · %"), Asset.deleted_at.is_(None)).one()
source_id = str(source.id)
source.deleted_at = datetime.now(timezone.utc) - timedelta(days=400)
db.commit()
counts = _run_cleanup(db)
db.commit()
print(json.dumps({"purged_asset": source_id, "assets": counts.assets, "versions": counts.versions,
                  "s3_deletes": counts.s3_deletes}))
