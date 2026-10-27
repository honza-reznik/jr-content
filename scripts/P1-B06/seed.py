"""Vymyšlená data pro Chromium, pouze vlastní ověřovací projekt P1-B06."""
import json
import os

from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.services import create_content
from apps.jrc_texts.services import create_document
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

assert os.environ.get("JRC_PROJECT") == "jrc-p1b06", "Pouze vlastní ověřovací projekt!"
organization = Organization.objects.create(name="P1-B06 local studio")
workspace = Workspace.objects.create(organization=organization, name="P1-B06 studio")
user = User.objects.create_user(email="editor-p1b06@example.invalid", name="Jamie", tos_accepted_at=timezone.now())
OrgMembership.objects.create(user=user, organization=organization, org_role="owner")
WorkspaceMembership.objects.create(user=user, workspace=workspace, workspace_role="owner")
user.last_workspace_id = workspace.pk
user.save(update_fields=["last_workspace_id"])
first = create_manual_channel(workspace, "instagram", "Studio IG")
post = create_content(workspace, "Unassigned studio idea", user)
create_manual_channel(workspace, "youtube", "Added later YT")
create_document(post.pk, user, markdown="# First idea\n\nThe channel can wait.")
other_workspace = Workspace.objects.create(organization=organization, name="P1-B06 other client")
WorkspaceMembership.objects.create(user=user, workspace=other_workspace, workspace_role="owner")
other_channel = create_manual_channel(other_workspace, "youtube", "Other client YT")
dated = {}
for width in (1440, 390):
    idea = create_content(workspace, f"Dated idea {width}", user)
    idea.proposed_publish_at = timezone.now()
    idea.save(update_fields=["proposed_publish_at"])
    dated[str(width)] = str(idea.pk)
http = Client()
http.force_login(user)
print(json.dumps({"workspace": str(workspace.pk), "post": str(post.pk), "channel": str(first.pk),
    "other_workspace": str(other_workspace.pk), "other_channel": str(other_channel.pk),
    "dated": dated, "date": timezone.now().date().isoformat(), "session": http.cookies["sessionid"].value}))
