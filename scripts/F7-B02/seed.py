"""Vymyšlené účty a obsah pro ověření schválení klientem; jen vlastní Docker projekt jrc-f7b02.

Spouští se v `manage.py shell` kontejneru jrc-bb-web. Volba `JRC_F7B02_STEP`:
  seed (výchozí)  účty, klient Ella (dvoustupňový režim), obsah ve fázi Review, druhý klient bez Elly
  rework          tým uloží novou verzi scénáře a pošle obsah znovu do Review
"""

import json
import os
import secrets

from django.utils import timezone

from apps.accounts.models import User
from apps.composer.models import Post
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.services import create_content, move_content, pipeline_stage
from apps.jrc_texts import services as texts
from apps.jrc_video.services import sync_workspace
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace

assert os.environ.get("JRC_PROJECT") == "jrc-f7b02"
organization, _ = Organization.objects.get_or_create(name="F7-B02 test studio")
step = os.environ.get("JRC_F7B02_STEP", "seed")


def person(workspace, key, role, name):
    user, _ = User.objects.get_or_create(
        email=f"{key}-f7b02@example.invalid", defaults={"name": name, "tos_accepted_at": timezone.now()}
    )
    password = secrets.token_urlsafe(24)
    user.set_password(password)
    user.save()
    OrgMembership.objects.get_or_create(
        organization=organization, user=user, defaults={"org_role": "owner" if role == "owner" else "member"}
    )
    WorkspaceMembership.objects.update_or_create(workspace=workspace, user=user, defaults={"workspace_role": role})
    return user, {"email": user.email, "password": password}


if step == "rework":
    workspace = Workspace.objects.get(name="F7-B02 Ella garden")
    owner = User.objects.get(email="editor-f7b02@example.invalid")
    post = Post.objects.get(workspace=workspace, title="Ella garden reel")
    document = post.jrc_texts.get(kind="script")
    draft = texts.edit(document.versions.filter(state="final").latest("number").pk, owner)
    texts.save(
        draft.pk,
        owner,
        expected_revision=0,
        markdown="SCENE ONE: Ella waters the roses at dawn.\nSCENE TWO: a short, quiet ending.",
    )
    move_content(post, "review", owner, expected_stage=pipeline_stage(post))
    print(json.dumps({"stage": pipeline_stage(post)}))
    raise SystemExit

# Opakované spuštění: obsah z předchozího běhu se smaže (jen v tomto ověřovacím projektu).
Post.objects.filter(workspace__organization=organization).delete()
workspace, _ = Workspace.objects.get_or_create(
    organization=organization, name="F7-B02 Ella garden", defaults={"approval_workflow_mode": "required_internal_and_client"}
)
Workspace.objects.filter(pk=workspace.pk).update(approval_workflow_mode="required_internal_and_client")
owner, owner_login = person(workspace, "editor", "owner", "Upload editor")
_, manager_login = person(workspace, "manager", "manager", "Team manager")
ella, client_login = person(workspace, "ella", "client", "Ella")
_, viewer_login = person(workspace, "viewer", "viewer", "Read only viewer")
if not SocialAccount.objects.filter(workspace=workspace).exists():
    create_manual_channel(workspace, "instagram", "Ella Instagram")
post = create_content(workspace, "Ella garden reel", owner)
document = texts.create_document(post.pk, owner, markdown="SCENE ONE: Ella waters the roses at dawn.")
variant = post.platform_posts.get()
texts.save_caption(post.pk, variant.pk, owner, "Roses at dawn. A slow morning in the garden. #garden")
move_content(post, "review", owner, expected_stage="idea")

# Druhý klient: Ella tam není členem, obsah čeká na jiného klienta.
other, _ = Workspace.objects.get_or_create(organization=organization, name="F7-B02 Other client")
Workspace.objects.filter(pk=other.pk).update(approval_workflow_mode="required_internal_and_client")
WorkspaceMembership.objects.update_or_create(workspace=other, user=owner, defaults={"workspace_role": "owner"})
if not SocialAccount.objects.filter(workspace=other).exists():
    create_manual_channel(other, "instagram", "Other Instagram")
foreign = create_content(other, "Foreign reel", owner)
move_content(foreign, "review", owner, expected_stage="idea")

sync_workspace(workspace.pk)
print(
    json.dumps(
        {
            "workspace": str(workspace.pk),
            "post": str(post.pk),
            "editor": owner_login,
            "manager": manager_login,
            "client": client_login,
            "viewer": viewer_login,
        }
    )
)
