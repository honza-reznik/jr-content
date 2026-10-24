"""Vymyšlené účty pro ověření SSO; jen vlastní Docker projekt."""

import json
import os
import secrets

from django.utils import timezone

from apps.accounts.models import User
from apps.jrc_video.models import VideoProject
from apps.jrc_video.services import sync_workspace
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

assert os.environ.get("JRC_PROJECT") == "jrc-f5b03"
organization, _ = Organization.objects.get_or_create(name="F5-B03 test studio")
user, _ = User.objects.get_or_create(
    email="sso-f5b03@example.invalid", defaults={"name": "SSO test member", "tos_accepted_at": timezone.now()}
)
password = secrets.token_urlsafe(24)
user.set_password(password)
user.save()
OrgMembership.objects.get_or_create(organization=organization, user=user, defaults={"org_role": "owner"})
workspace, _ = Workspace.objects.get_or_create(organization=organization, name="F5-B03 video client")
WorkspaceMembership.objects.get_or_create(workspace=workspace, user=user, defaults={"workspace_role": "owner"})
sync_workspace(workspace.pk)
print(json.dumps({"email": user.email, "password": password, "project": str(VideoProject.objects.get(workspace=workspace).ff_project_id)}))
