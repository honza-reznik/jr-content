"""Živé ověření Brightbean → FreeFrame; spouští check-sync.sh jen v Dockeru."""

import os
import sys
import uuid

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.jrc")
import django

django.setup()

from django.core.management import call_command
from django.test import override_settings
from apps.accounts.models import User
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace
from apps.members.models import WorkspaceMembership
from apps.jrc_video.models import VideoAccount, VideoProject, VideoSyncRequest
from apps.jrc_video.client import FreeFrameClient

WORKSPACE = uuid.UUID("f5b01000-0000-4000-8000-000000000001")
OTHER_WORKSPACE = uuid.UUID("f5b01000-0000-4000-8000-000000000002")
USERS = [uuid.UUID(f"f5b01000-0000-4000-8000-{number:012}") for number in (101, 102, 103, 104)]
stage = sys.argv[1]

if stage == "initial":
    org, _ = Organization.objects.get_or_create(name="F5-B01 live verification")
    workspace = Workspace.objects.create(id=WORKSPACE, organization=org, name="Video client")
    other = Workspace.objects.create(id=OTHER_WORKSPACE, organization=org, name="Other client")
    for identity, role in zip(USERS, ["owner", "client", "viewer", "client"]):
        # Upstream automatically creates a personal workspace on User.save().
        # This fixture represents invited users already belonging to the client.
        with override_settings(JRC_FF_SYNC_ENABLED=False):
            user = User.objects.create_user(id=identity, email=f"f5b01-{role}-{identity}@example.invalid", name=f"Test {role}")
            Workspace.objects.filter(memberships__user=user).delete()
        WorkspaceMembership.objects.create(
            workspace=other if identity == USERS[-1] else workspace, user=user, workspace_role=role,
        )
    assert not VideoSyncRequest.objects.exists()
    ids = list(VideoAccount.objects.order_by("user_id").values_list("ff_user_id", flat=True))
    project_id = VideoProject.objects.get(workspace=workspace).ff_project_id
    # Simulate loss of local mappings after a partial restore. Remote media are retained.
    VideoAccount.objects.all().delete()
    VideoProject.objects.all().delete()
    call_command("jrc_sync_freeframe")
    call_command("jrc_sync_freeframe")
    assert list(VideoAccount.objects.order_by("user_id").values_list("ff_user_id", flat=True)) == ids
    assert VideoProject.objects.get(workspace=workspace).ff_project_id == project_id
    assert VideoAccount.objects.count() == 4 and VideoProject.objects.count() == 2
    client = User.objects.get(pk=USERS[1])
    client.name = "Renamed client"
    client.email = "f5b01-renamed@example.invalid"
    client.save(update_fields=["name", "email"])
    workspace.name = "Renamed project"
    workspace.save(update_fields=["name"])
    api = FreeFrameClient()
    remote_users = api.request("GET", "/admin/users")
    remote_client = next(user for user in remote_users if user["id"] == str(VideoAccount.objects.get(user=client).ff_user_id))
    assert remote_client["name"] == client.name and remote_client["email"] == client.email
    assert remote_client["invite_token"] is None
    assert api.request("GET", f"/projects/{project_id}")["name"] == workspace.name
    # Real refused TCP connection, then repair through the public management command.
    with override_settings(JRC_FF_API_URL="http://127.0.0.1:9"):
        membership = WorkspaceMembership.objects.get(workspace=workspace, user=client)
        membership.workspace_role = "viewer"
        membership.save(update_fields=["workspace_role"])
    assert VideoSyncRequest.objects.get(workspace=workspace).last_error
    call_command("jrc_sync_freeframe", pending=True)
    assert not VideoSyncRequest.objects.exists()
    membership.workspace_role = "client"
    membership.save(update_fields=["workspace_role"])
    # A conflicting email (other letter case) must not take over the service account,
    # and the failing account must not block the synchronization of the other members.
    from django.conf import settings
    with override_settings(JRC_FF_SYNC_ENABLED=False):
        conflict = User.objects.create_user(email=settings.JRC_FF_SYNC_EMAIL.upper(), name="Conflict")
        Workspace.objects.filter(memberships__user=conflict).delete()
    bad_member = WorkspaceMembership.objects.create(workspace=workspace, user=conflict, workspace_role="owner")
    assert "HTTP 409" in VideoSyncRequest.objects.get(workspace=workspace).last_error
    assert not VideoAccount.objects.filter(user=conflict).exists()
    remote_ids = {m["user_id"] for m in api.request("GET", f"/projects/{project_id}/members")}
    assert {str(a.ff_user_id) for a in VideoAccount.objects.filter(user_id__in=USERS[:3])} <= remote_ids
    bad_member.delete()
    conflict.delete()
    assert not VideoSyncRequest.objects.exists()
elif stage == "changed":
    member = WorkspaceMembership.objects.get(workspace_id=WORKSPACE, user_id=USERS[1])
    member.workspace_role = "viewer"
    member.save(update_fields=["workspace_role"])
    WorkspaceMembership.objects.get(workspace_id=WORKSPACE, user_id=USERS[2]).delete()
    team = User.objects.get(pk=USERS[0])
    team.is_active = False
    team.save(update_fields=["is_active"])
    assert not VideoSyncRequest.objects.exists()
elif stage == "archived":
    workspace = Workspace.objects.get(pk=WORKSPACE)
    workspace.is_archived = True
    workspace.save(update_fields=["is_archived"])
    team = User.objects.get(pk=USERS[0])
    team.is_active = True
    team.save(update_fields=["is_active"])
    assert not VideoSyncRequest.objects.exists()
elif stage == "deleted":
    Workspace.objects.get(pk=WORKSPACE).delete()
else:
    raise AssertionError(stage)
print(f"OK Brightbean live sync: {stage}")
