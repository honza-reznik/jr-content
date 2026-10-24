"""Create isolated test data for the F2-B14 Chromium acceptance check."""

import json
import secrets
from pathlib import Path

from django.utils import timezone

from apps.accounts.models import User
from apps.media_library.models import MediaAsset
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace


email = "f2b14-csp@example.test"
User.objects.filter(email=email).delete()
password = secrets.token_urlsafe(24)
user = User.objects.create_user(
    email=email,
    password=password,
    tos_accepted_at=timezone.now(),
)
organization = Organization.objects.create(name="F2-B14 CSP test")
workspace = Workspace.objects.create(organization=organization, name="F2-B14 CSP test")
OrgMembership.objects.create(
    user=user,
    organization=organization,
    org_role=OrgMembership.OrgRole.OWNER,
)
WorkspaceMembership.objects.create(
    user=user,
    workspace=workspace,
    workspace_role=WorkspaceMembership.WorkspaceRole.OWNER,
)
asset = MediaAsset.objects.create(
    organization=organization,
    workspace=workspace,
    uploaded_by=user,
    file="media_library/tests/f2b14-csp.png",
    filename="F2-B14 CSP test image.png",
    media_type=MediaAsset.MediaType.IMAGE,
    mime_type="image/png",
    file_size=128,
    source="upload",
)
output = Path("/tmp/F2-B14-credentials.json")
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(
    json.dumps(
        {
            "email": email,
            "password": password,
            "workspace_id": str(workspace.id),
            "asset_id": str(asset.id),
        }
    )
)
