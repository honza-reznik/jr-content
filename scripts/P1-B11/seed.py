"""P1-B11: vymyšlený nový owner (createsuperuser) a pozvánky jen pro ověřovací projekt jrc-p1b11 (vypisuje JSON)."""

import json
import os
import secrets
from datetime import timedelta

from django.core.management import call_command
from django.utils import timezone

from apps.accounts.models import User
from apps.members.models import Invitation, OrgMembership
from apps.workspaces.models import Workspace

assert os.environ.get("JRC_PUBLIC_ORIGIN", "").endswith(":13543"), "Pouze vlastní ověřovací projekt jrc-p1b11!"
suffix = secrets.token_hex(3)
email = f"owner-{suffix}@example.invalid"
password = secrets.token_urlsafe(18)
os.environ["DJANGO_SUPERUSER_PASSWORD"] = password
call_command("createsuperuser", interactive=False, email=email, verbosity=0)
owner = User.objects.get(email=email)
assert owner.tos_accepted_at is None
organization = OrgMembership.objects.get(user=owner).organization
workspace = Workspace.objects.filter(organization=organization).first()
invites = {}
for width in (1440, 390):
    invitation = Invitation.objects.create(
        organization=organization,
        email=f"client-{width}-{suffix}@example.invalid",
        org_role=OrgMembership.OrgRole.MEMBER,
        workspace_assignments=[{"workspace_id": str(workspace.pk), "role": "client"}] if workspace else [],
        invited_by=owner,
        expires_at=timezone.now() + timedelta(days=1),
    )
    invites[str(width)] = {"token": invitation.token, "email": invitation.email}
print("SEED=" + json.dumps({"owner": {"email": email, "password": password}, "invites": invites}))
