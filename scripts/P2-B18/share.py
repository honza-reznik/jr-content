"""Zkouška skutečného sdíleného odkazu jen v izolovaném projektu."""
import json
import secrets
import sys
from apps.api.database import SessionLocal
from apps.api.models.asset import Asset
from apps.api.models.share import ShareLink
from apps.api.config import settings
assert ':13918/' in settings.frontend_url
f = json.loads(input())
with SessionLocal() as db:
    asset = db.query(Asset).filter(Asset.id == f['asset']).one()
    if sys.argv[1] == 'create':
        link = ShareLink(asset_id=asset.id, created_by=asset.created_by, token=secrets.token_urlsafe(32), allow_download=True)
        db.add(link)
        db.commit()
        print(json.dumps({'token': link.token}))
    else:
        db.query(ShareLink).filter(ShareLink.asset_id == asset.id).update({'allow_download': False})
        db.commit()
        print('OK sdílenému odkazu odebráno stahování')
