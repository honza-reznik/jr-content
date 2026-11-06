"""Vymyšlené účty a jeden obsah s kanálem, jen pro vlastní projekt bloku P1-B26."""
import io
import json
import os
import runpy
from contextlib import redirect_stdout

from apps.accounts.models import User
from apps.jrc_production.services import create_content

assert os.environ.get("JRC_PROJECT") == "jrc-p1b26"
with redirect_stdout(io.StringIO()):
    seed = runpy.run_path("/jrc-scripts/F10-B02/seed.py")
owner = User.objects.get(email=seed["admin"]["email"])
posts = [
    create_content(seed["workspace"], f"P1-B26 video {name}", owner, social_account_ids=[seed["account"].pk])
    for name in ("upload", "player")
]
print("FIXTURE=" + json.dumps({
    "workspace": str(seed["workspace"].pk), "post": str(posts[1].pk), "upload_post": str(posts[0].pk),
    "admin": seed["admin"], "client": seed["ella"],
}))
