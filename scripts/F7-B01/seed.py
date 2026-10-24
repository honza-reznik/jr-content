"""Vymyšlený klient a dočasná relace jen pro vlastní ověřovací projekt."""

import json
import os
from pathlib import Path

from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.services import create_content
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

assert os.environ.get("JRC_PROJECT") == "jrc-f7b01", "Pouze vlastní ověřovací projekt!"
organization = Organization.objects.create(name="F7-B01 local studio")
workspace = Workspace.objects.create(organization=organization, name="F7-B01 test client")
owner = User.objects.create_user(email="owner-f7b01@example.invalid", tos_accepted_at=timezone.now())
reader = User.objects.create_user(email="client-f7b01@example.invalid", tos_accepted_at=timezone.now())
for person, role in ((owner, "owner"), (reader, "client")):
    OrgMembership.objects.create(organization=organization, user=person, org_role="member")
    WorkspaceMembership.objects.create(workspace=workspace, user=person, workspace_role=role)
create_manual_channel(workspace, "instagram", "Test Instagram")
post = create_content(workspace, "A morning in the garden", owner)
post.internal_notes = "SECRET INTERNAL NOTE"
post.save(update_fields=["internal_notes"])
http = Client()
http.force_login(reader)
Path("/tmp/f7b01-session.json").write_text(
    json.dumps({"workspace": str(workspace.pk), "post": str(post.pk), "session": http.cookies["sessionid"].value})
)
print("Vymyšlený klient a dočasná relace připraveny.")
