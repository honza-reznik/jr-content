"""Jen v postradatelné databázi projektu jrc-f2b10; žádné skutečné účty."""
import json
from pathlib import Path

from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

user, _ = User.objects.get_or_create(email="ui@example.invalid", defaults={"name": "Local Editor", "tos_accepted_at": timezone.now()})
org, _ = Organization.objects.get_or_create(name="Local Studio")
workspace, _ = Workspace.objects.get_or_create(organization=org, name="Content Studio")
OrgMembership.objects.get_or_create(user=user, organization=org, defaults={"org_role": "owner"})
WorkspaceMembership.objects.get_or_create(user=user, workspace=workspace, defaults={"workspace_role": "owner"})
user.last_workspace_id = workspace.id
user.save(update_fields=["last_workspace_id"])
client = Client()
client.force_login(user)
# Přenos pouze do připojeného worktree; výstup je ignorovaný, nesmí do gitu/KB.
Path("/app/media/f2b10-ui-session.json").write_text(json.dumps({"workspace": str(workspace.id), "session": client.cookies["sessionid"].value}))
print("Připraven fiktivní místní uživatel a pracovní prostor.")
