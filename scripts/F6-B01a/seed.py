"""Vymyšlená testovací data pro Chromium ověření F6-B01a (jen ověřovací projekt)."""
import json, secrets
from django.utils import timezone
from apps.accounts.models import User
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.services import create_content

email = "f6b01a@example.test"
User.objects.filter(email=email).delete()
password = secrets.token_urlsafe(24)
user = User.objects.create_user(email=email, password=password, tos_accepted_at=timezone.now())
org = Organization.objects.create(name="F6-B01a test")
ws = Workspace.objects.create(organization=org, name="F6-B01a test")
OrgMembership.objects.create(user=user, organization=org, org_role=OrgMembership.OrgRole.OWNER)
WorkspaceMembership.objects.create(user=user, workspace=ws, workspace_role=WorkspaceMembership.WorkspaceRole.OWNER)
create_manual_channel(ws, "instagram", "Test Instagram")
post = create_content(ws, "Garden reel", user)
print(json.dumps({"email": email, "password": password, "workspace_id": str(ws.id), "post_id": str(post.id)}))
