"""Vymyšlené účty a obsah pro ověření záložky Video; jen vlastní Docker projekt jrc-f5b08."""

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

assert os.environ.get("JRC_PROJECT") in {"jrc-f5b08", "jrc-f5b09a", "jrc-f5b09b"}
organization, _ = Organization.objects.get_or_create(name="F5-B08 test studio")
workspace, _ = Workspace.objects.get_or_create(organization=organization, name="F5-B08 Ella garden")


def person(key, role, name):
    user, _ = User.objects.get_or_create(
        email=f"{key}-f5b08@example.invalid", defaults={"name": name, "tos_accepted_at": timezone.now()}
    )
    password = secrets.token_urlsafe(24)
    user.set_password(password)
    user.save()
    OrgMembership.objects.get_or_create(
        organization=organization, user=user, defaults={"org_role": "owner" if role == "owner" else "member"}
    )
    WorkspaceMembership.objects.update_or_create(workspace=workspace, user=user, defaults={"workspace_role": role})
    return user, {"email": user.email, "password": password}


owner, owner_login = person("editor", "owner", "Upload editor")
_, manager_login = person("manager", "manager", "Team manager")
_, client_login = person("ella", "client", "Ella")
_, viewer_login = person("viewer", "viewer", "Read only viewer")
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
            "viewer": viewer_login,
        }
    )
)
