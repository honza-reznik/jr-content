"""Vymyšlené účty a obsah pro ověření nahrání verze videa; jen vlastní Docker projekt jrc-f5b05."""

import json
import os
import secrets

from django.utils import timezone

from apps.accounts.models import User
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.services import create_content
from apps.jrc_video.models import VideoProject
from apps.jrc_video.services import sync_workspace
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace

assert os.environ.get("JRC_PROJECT") == "jrc-f5b05"
organization, _ = Organization.objects.get_or_create(name="F5-B05 test studio")
workspace, _ = Workspace.objects.get_or_create(organization=organization, name="F5-B05 video client")


def person(key, role, name):
    user, _ = User.objects.get_or_create(
        email=f"{key}-f5b05@example.invalid", defaults={"name": name, "tos_accepted_at": timezone.now()}
    )
    password = secrets.token_urlsafe(24)
    user.set_password(password)
    user.save()
    OrgMembership.objects.get_or_create(organization=organization, user=user, defaults={"org_role": "owner" if role == "owner" else "member"})
    WorkspaceMembership.objects.update_or_create(workspace=workspace, user=user, defaults={"workspace_role": role})
    return user, {"email": user.email, "password": password}


owner, owner_login = person("editor", "owner", "Upload editor")
_, manager_login = person("manager", "manager", "Second manager")
_, client_login = person("client", "client", "Ella client")
if not SocialAccount.objects.filter(workspace=workspace).exists():
    create_manual_channel(workspace, "instagram", "Ella Instagram")
    create_manual_channel(workspace, "linkedin_personal", "Ella LinkedIn")
post = create_content(workspace, "Ella garden reel", owner)
sync_workspace(workspace.pk)
print(
    json.dumps(
        {
            "workspace": str(workspace.pk),
            "post": str(post.pk),
            "project": str(VideoProject.objects.get(workspace=workspace).ff_project_id),
            "editor": owner_login,
            "manager": manager_login,
            "client": client_login,
        }
    )
)
