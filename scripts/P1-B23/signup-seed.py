"""Připraví dvě vymyšlené pozvánky jen ve vlastním ověřovacím projektu."""

import json
import secrets
from datetime import timedelta
from pathlib import Path

from django.utils import timezone

from apps.members.models import Invitation

path = Path("/app/.p1b23.env.local")
data = json.loads(path.read_text())
data["signups"] = []
for role in ["manager", "client"]:
    invitation = Invitation.objects.create(
        organization_id=data["organization"],
        email=f"p1b23-signup-{role}-{secrets.token_hex(4)}@example.invalid",
        expires_at=timezone.now() + timedelta(days=1),
        workspace_assignments=[{"workspace_id": data["workspace"], "role": role}],
    )
    data["signups"].append(
        {"role": role, "email": invitation.email, "token": invitation.token, "password": secrets.token_urlsafe(24)}
    )
path.write_text(json.dumps(data))
path.chmod(0o600)
print("Připraveny dvě místní pozvánky; bez odeslání e-mailu.")
