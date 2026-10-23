"""Vymyšlená data pro prohlížeč; spouštět jen v ověřovacím Docker projektu."""

import json
import os
from datetime import timedelta

from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.composer.models import PlatformPost
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.models import PipelineStage, PostProduction
from apps.jrc_production.services import create_content, move_content
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

assert os.environ.get("JRC_PROJECT") == "jrc-f3b02", "Pouze vlastní ověřovací projekt!"
organization, _ = Organization.objects.get_or_create(name="JR Studio · F3-B02 demo")
admin, _ = User.objects.get_or_create(
    email="admin-f3b02@example.invalid", defaults={"name": "Jamie", "tos_accepted_at": timezone.now()}
)
ella, _ = User.objects.get_or_create(
    email="ella-f3b02@example.invalid", defaults={"name": "Ella", "tos_accepted_at": timezone.now()}
)
for person, role in ((admin, "owner"), (ella, "member")):
    OrgMembership.objects.get_or_create(organization=organization, user=person, defaults={"org_role": role})
workspace, _ = Workspace.objects.get_or_create(
    organization=organization,
    name="Ella",
    defaults={"timezone": "Europe/Prague", "approval_workflow_mode": "required_internal_and_client"},
)
for person, role in ((admin, "owner"), (ella, "client")):
    WorkspaceMembership.objects.get_or_create(workspace=workspace, user=person, defaults={"workspace_role": role})
    person.last_workspace_id = workspace.pk
    person.save(update_fields=["last_workspace_id"])
if not workspace.social_accounts.exists():
    create_manual_channel(workspace, "instagram", "Instagram", display_name="Instagram")
    create_manual_channel(workspace, "custom", "Forendors", display_name="Forendors")

titles = [
    "Five little studio rituals",
    "A morning with Ella",
    "Behind the scenes: autumn collection",
    "How I style my workspace",
    "Three things I wish I knew",
    "Meet the new collection",
    "Your questions, answered",
    "A little thank you",
]
for stage, title in zip(PipelineStage.values, titles, strict=True):
    if workspace.posts.filter(title=title).exists():
        continue
    post = create_content(workspace, title, admin)
    if stage in {"idea", "script", "shooting", "editing"}:
        PostProduction.objects.filter(post=post).update(stage=stage)
        continue
    move_content(post, "review", admin, expected_stage="idea")
    if stage == "review":
        continue
    move_content(post, "approved", admin, expected_stage="review")
    move_content(post, "approved", ella, expected_stage="review")
    if stage == "approved":
        continue
    move_content(post, "ready", admin, expected_stage="approved", scheduled_at=timezone.now() + timedelta(days=3))
    if stage == "published":
        # Pouze vzorek již publikovaného obsahu; vlastní publikace je blok F8-B02.
        PlatformPost.objects.filter(post=post).update(status="published", published_at=timezone.now())

sessions = {}
for key, person in (("admin", admin), ("client", ella)):
    client = Client()
    client.force_login(person)
    sessions[key] = client.cookies["sessionid"].value
print("F3B02_FIXTURE=" + json.dumps({"workspace": str(workspace.pk), "sessions": sessions}))
