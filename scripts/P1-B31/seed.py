"""Vymyšlené účty a sedm čekajících obsahů, jen pro vlastní projekt bloku."""
import io
import json
import os
import runpy
from contextlib import redirect_stdout
from datetime import timedelta

from django.utils import timezone

from apps.accounts.models import User
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.services import create_content

assert os.environ.get("JRC_PROJECT") == "jrc-p1b31"
with redirect_stdout(io.StringIO()):
    seed = runpy.run_path("/jrc-scripts/F10-B02/seed.py")
owner = User.objects.get(email=seed["admin"]["email"])
create_manual_channel(seed["workspace"], "instagram", "P1-B31 Dlouhý název kanálu " + "x" * 73)
posts = []
for index in range(7):
    post = create_content(
        seed["workspace"], f"P1-B31 mobilní obsah {index + 1}", owner,
        social_account_ids=[seed["account"].pk],
    )
    post.platform_posts.update(status="pending_review", scheduled_at=timezone.now() + timedelta(days=index))
    posts.append(post)
print("FIXTURE=" + json.dumps({
    "workspace": str(post.workspace_id), "post": str(posts[0].pk),
    "admin": seed["admin"], "client": seed["ella"],
}))
