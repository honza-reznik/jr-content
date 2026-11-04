"""Vymyšlená data pro Chromium, pouze vlastní ověřovací projekt P1-B09."""

import json
import os

from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production import shotlist
from apps.jrc_production.services import create_content
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

assert os.environ.get("JRC_PROJECT") == "jrc-p1b09", "Pouze vlastní ověřovací projekt!"
run = timezone.now().strftime("%H%M%S")
organization = Organization.objects.create(name=f"P1-B09 studio {run}")
workspace = Workspace.objects.create(organization=organization, name="P1-B09 Ella")
owner = User.objects.create_user(
    email=f"owner-p1b09-{run}@example.invalid", name="Jamie Owner", tos_accepted_at=timezone.now()
)
client_user = User.objects.create_user(
    email=f"client-p1b09-{run}@example.invalid", name="Ella Client", tos_accepted_at=timezone.now()
)
OrgMembership.objects.create(user=owner, organization=organization, org_role="owner")
OrgMembership.objects.create(user=client_user, organization=organization, org_role="member")
WorkspaceMembership.objects.create(user=owner, workspace=workspace, workspace_role="owner")
WorkspaceMembership.objects.create(user=client_user, workspace=workspace, workspace_role="client")
for user in (owner, client_user):
    user.last_workspace_id = workspace.pk
    user.save(update_fields=["last_workspace_id"])
channel = create_manual_channel(workspace, "instagram", "Ella IG")
posts = {}
for width in (1440, 390):
    post = create_content(workspace, f"Pottery reel {width}", owner, social_account_ids=[channel.pk])
    first = shotlist.add_item(post, owner, "Wide shot of the studio", "Morning light from the window")
    shotlist.add_item(post, owner, "Close-up of hands shaping the clay")
    shotlist.update_item(post, first.pk, owner, is_done=True)
    posts[str(width)] = str(post.pk)
sessions = {}
for name, user in (("owner", owner), ("client", client_user)):
    http = Client()
    http.force_login(user)
    sessions[name] = http.cookies["sessionid"].value
print(json.dumps({"workspace": str(workspace.pk), "posts": posts, "sessions": sessions}))
