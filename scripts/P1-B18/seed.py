"""Vymyšlené účty a obsah pro ověření němého videa, pouze ve vlastním projektu."""
import json
import os
import runpy

from apps.accounts.models import User
from apps.jrc_production.services import create_content

assert os.environ.get("JRC_PROJECT") == "jrc-p1b18"
seed = runpy.run_path("/jrc-scripts/F10-B02/seed.py")
post = create_content(
    seed["workspace"], "P1-B18 video bez zvuku", User.objects.get(email=seed["admin"]["email"]),
    social_account_ids=[seed["account"].pk],
)
post.platform_posts.update(status="pending_client")
print(json.dumps({
    "workspace": str(post.workspace_id), "post": str(post.pk),
    "admin": seed["admin"], "client": seed["ella"],
}))
