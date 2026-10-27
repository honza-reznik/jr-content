"""Lokální objekty pro ověření soukromého čtení; pouze projekt P1-B03."""
import json
import os
import secrets

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.approvals.models import PostComment
from apps.composer.models import Post
from apps.media_library.models import MediaAsset, MediaAssetVersion
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

assert os.environ.get("JRC_PROJECT") == "jrc-p1b03"
org = Organization.objects.create(name="P1-B03 private files")
workspace = Workspace.objects.create(organization=org, name="P1-B03")
people = {}
for role in ("owner", "client", "stranger"):
    password = secrets.token_urlsafe(24)
    user = User.objects.create_user(email=f"{role}-{secrets.token_hex(5)}@p1b03.example.invalid", password=password,
                                    name=role, tos_accepted_at=timezone.now())
    if role != "stranger":
        OrgMembership.objects.create(organization=org, user=user, org_role="owner" if role == "owner" else "member")
        WorkspaceMembership.objects.create(workspace=workspace, user=user, workspace_role=role)
    people[role] = {"id": str(user.pk), "email": user.email, "password": password}
owner = User.objects.get(pk=people["owner"]["id"])
files = []
payload = b"0123456789" * 30000

def store(key, internal=False):
    key = default_storage.save(f"{key}/{secrets.token_hex(8)}.bin", ContentFile(payload))
    files.append({"url": default_storage.url(key), "internal": internal})
    return key

asset = MediaAsset.objects.create(organization=org, workspace=workspace, uploaded_by=owner,
    file=store("media_library"), thumbnail=store("media_library/thumbs"), filename="source.bin")
MediaAssetVersion.objects.create(media_asset=asset, version_number=1,
    file=store("media_library/versions"), thumbnail=store("media_library/thumbs"))
owner.avatar = store("avatars")
owner.save(update_fields=["avatar"])
workspace.icon = store("workspaces/icons")
workspace.save(update_fields=["icon"])
shared = MediaAsset.objects.create(organization=org, uploaded_by=owner, file=store("media_library/shared"), filename="shared.bin")
post = Post.objects.create(workspace=workspace, author=owner)
for visibility in ("external", "internal"):
    PostComment.objects.create(post=post, author=owner, body=visibility, visibility=visibility,
        attachment=store("comment_attachments", visibility == "internal"))
print(json.dumps({"people": people, "workspace": str(workspace.pk), "files": files,
    "range": default_storage.url(asset.file.name),
    "download": reverse("media_library:asset_download", kwargs={"workspace_id": workspace.pk, "asset_id": asset.pk})}))
