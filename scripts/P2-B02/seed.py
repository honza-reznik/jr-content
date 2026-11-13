"""Vymyšlená data pouze ve vlastním projektu P2-B02."""
import json
import os
import secrets

from django.utils import timezone
from apps.accounts.models import User
from apps.jrc_video.models import VideoAccount, VideoProject
from apps.jrc_video.services import sync_workspace
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

assert os.environ.get("JRC_PROJECT") == "jrc-p2b02"
org = Organization.objects.create(name="P2-B02 test studio")
workspace = Workspace.objects.create(organization=org, name="P2-B02 video client")
users = {}
for role in ("owner", "client", "outsider"):
    user = User.objects.create(email=f"{role}-p2b02@example.invalid", name=f"P2-B02 {role}", tos_accepted_at=timezone.now())
    password = secrets.token_urlsafe(24)
    user.set_password(password)
    user.save()
    if role != "outsider":
        OrgMembership.objects.create(organization=org, user=user, org_role="owner" if role == "owner" else "member")
        WorkspaceMembership.objects.create(workspace=workspace, user=user, workspace_role=role)
    users[role] = {"email": user.email, "password": password}
sync_workspace(workspace.pk)
print(json.dumps({"users": users, "project": str(VideoProject.objects.get(workspace=workspace).ff_project_id),
                  "owner_id": str(VideoAccount.objects.get(user__email=users["owner"]["email"]).ff_user_id)}))
