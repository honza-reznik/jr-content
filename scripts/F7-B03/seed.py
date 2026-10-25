"""Vymyšlené účty a události pro vizuální kontrolu upozornění; jen vlastní Docker projekt jrc-f7b03.

Spouští se v `manage.py shell` kontejneru jrc-bb-web. Dvoustupňový režim, klient Ella:
  1. tým uloží scénář a popisek (obsah je ve fázi Idea, Ella nic nedostane)
  2. obsah jde do Review (upozornění jen týmu), manažer schválí (Ella dostane žádost o schválení)
  3. tým uloží novou verzi scénáře (už dostane i Ella a čtenář) a hotové video
  4. Ella požádá o změny (tým dostane upozornění s komentářem)
"""

import json
import os
import secrets
import uuid

from django.utils import timezone

from apps.accounts.models import User
from apps.composer.models import Post
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.services import create_content, move_content, request_content_changes
from apps.jrc_texts import services as texts
from apps.jrc_video.models import VideoAsset, VideoVersion
from apps.jrc_video.uploads import _apply_remote
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.notifications.models import Notification
from apps.organizations.models import Organization
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace

assert os.environ.get("JRC_PROJECT") == "jrc-f7b03"
organization, _ = Organization.objects.get_or_create(name="F7-B03 test studio")


def person(workspace, key, role, name):
    user, _ = User.objects.get_or_create(
        email=f"{key}-f7b03@example.invalid", defaults={"name": name, "tos_accepted_at": timezone.now()}
    )
    password = secrets.token_urlsafe(24)
    user.set_password(password)
    user.save()
    OrgMembership.objects.get_or_create(
        organization=organization, user=user, defaults={"org_role": "owner" if role == "owner" else "member"}
    )
    WorkspaceMembership.objects.update_or_create(workspace=workspace, user=user, defaults={"workspace_role": role})
    return user, {"email": user.email, "password": password}


# Opakované spuštění: obsah a upozornění z předchozího běhu se smažou (jen v tomto ověřovacím projektu).
Post.objects.filter(workspace__organization=organization).delete()
Notification.objects.filter(user__email__endswith="-f7b03@example.invalid").delete()
workspace, _ = Workspace.objects.get_or_create(organization=organization, name="F7-B03 Ella garden")
Workspace.objects.filter(pk=workspace.pk).update(approval_workflow_mode="required_internal_and_client")
workspace.refresh_from_db()
owner, owner_login = person(workspace, "editor", "owner", "Upload editor")
manager, manager_login = person(workspace, "manager", "manager", "Team manager")
ella, client_login = person(workspace, "ella", "client", "Ella")
viewer, viewer_login = person(workspace, "viewer", "viewer", "Read only viewer")
if not SocialAccount.objects.filter(workspace=workspace).exists():
    create_manual_channel(workspace, "instagram", "Ella Instagram")

post = create_content(workspace, "Ella garden reel", owner)
document = texts.create_document(post.pk, owner, markdown="")
draft = texts.edit(document.versions.get(state="final").pk, owner)
texts.save(draft.pk, owner, expected_revision=0, markdown="SCENE ONE: Ella waters the roses at dawn.")
variant = post.platform_posts.get()
texts.save_caption(post.pk, variant.pk, owner, "Roses at dawn. A slow morning in the garden. #garden")
assert not Notification.objects.filter(user=ella).exists(), "Ella nesmí nic dostat ve fázi Idea"

move_content(post, "review", owner, expected_stage="idea")
move_content(post, "approved", manager, expected_stage="review")
draft = texts.edit(document.versions.filter(state="final").latest("number").pk, owner)
texts.save(draft.pk, owner, expected_revision=0, markdown="SCENE ONE: Ella waters the roses at dawn.\nSCENE TWO: a quiet ending.")
video = VideoAsset.objects.create(platform_post=variant, ff_asset_id=uuid.uuid4())
version = VideoVersion.objects.create(
    video=video, number=1, ff_version_id=uuid.uuid4(), status="processing", uploaded_by=owner
)
_apply_remote(version, {"processing_status": "ready", "files": [{"duration_seconds": 20.0}]})
request_content_changes(post, ella, "Please cut the ending at 0:17.", expected_stage="review")

print(json.dumps({
    "workspace": str(workspace.pk), "post": str(post.pk),
    "editor": owner_login, "manager": manager_login, "client": client_login, "viewer": viewer_login,
}))
