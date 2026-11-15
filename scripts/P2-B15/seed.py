"""P2-B15: vymyšlené účty a obsah se čtyřmi výstupy pro ověření „Use for another output…“; jen jrc-p2b15."""

import json
import os
import secrets

from django.utils import timezone

from apps.accounts.models import User
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.services import add_content_channel, create_content
from apps.jrc_video.models import VideoProject
from apps.jrc_video.services import sync_workspace
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

assert os.environ.get("JRC_PROJECT") == "jrc-p2b15"
organization, _ = Organization.objects.get_or_create(name="P2-B15 test studio")
workspace, _ = Workspace.objects.get_or_create(
    organization=organization, name="Ella garden", defaults={"approval_workflow_mode": "required_internal"}
)


def person(key, role, name):
    user, _ = User.objects.get_or_create(
        email=f"{key}-p2b15@example.invalid", defaults={"name": name, "tos_accepted_at": timezone.now()}
    )
    password = secrets.token_urlsafe(24)
    user.set_password(password)
    user.save()
    OrgMembership.objects.get_or_create(
        organization=organization, user=user, defaults={"org_role": "owner" if role == "owner" else "member"}
    )
    WorkspaceMembership.objects.update_or_create(workspace=workspace, user=user, defaults={"workspace_role": role})
    return user, {"email": user.email, "password": password}


owner, owner_login = person("owner", "owner", "Honza Team")
_, editor_login = person("editor", "editor", "Editor Eva")
_, client_login = person("ella", "client", "Ella")
_, viewer_login = person("viewer", "viewer", "Viewer Vít")
youtube = create_manual_channel(workspace, "youtube", "Ella na YouTube")
instagram = create_manual_channel(
    workspace, "instagram", "Ella Šamánková – zahrada, bylinky a pomalý život na venkově (dlouhý název)"
)
post = create_content(workspace, "Garden tour in November", owner, social_account_ids=[youtube.pk, instagram.pk])
add_content_channel(post, youtube.pk, owner, "short_form")
add_content_channel(post, instagram.pk, owner, "story")
sync_workspace(workspace.pk)
print(json.dumps({
    "workspace": str(workspace.pk),
    "post": str(post.pk),
    "project": str(VideoProject.objects.get(workspace=workspace).ff_project_id),
    "owner": owner_login, "editor": editor_login, "client": client_login, "viewer": viewer_login,
}))
