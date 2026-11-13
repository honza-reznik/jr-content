"""Synthetic browser data; run only in jrc-p2b04 via manage.py shell."""
import json
import os
import secrets
from pathlib import Path

from django.utils import timezone

from apps.accounts.models import User
from apps.composer.models import ContentCategory
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production import shotlist
from apps.jrc_production.services import create_content
from apps.jrc_texts import services
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

assert os.environ["APP_URL"] == "http://127.0.0.1:13904"
assert not os.environ.get("JRC_SMTP_HOST")
organization = Organization.objects.create(name="P2-B04 test")
workspace = Workspace.objects.create(organization=organization, name="Synthetic client P2-B04", approval_workflow_mode="optional")
credentials = {}
for role in ("owner", "client", "viewer"):
    password = secrets.token_urlsafe(24)
    user, _ = User.objects.get_or_create(email=f"p2b04-{role}@example.invalid", defaults={"name": f"Test {role.title()}", "tos_accepted_at": timezone.now()})
    user.set_password(password)
    user.save()
    OrgMembership.objects.create(user=user, organization=organization, org_role="owner" if role == "owner" else "member")
    WorkspaceMembership.objects.create(user=user, workspace=workspace, workspace_role=role)
    credentials[role] = {"email": user.email, "password": password}
owner = User.objects.get(email=credentials["owner"]["email"])
account = create_manual_channel(workspace, "instagram", "Test Instagram")
post = create_content(workspace, "A long content title with readable words for desktop and mobile", owner, social_account_ids=[account.pk])
category = ContentCategory.objects.create(workspace=workspace, name="Behind the scenes")
ContentCategory.objects.create(workspace=workspace, name="Long category " + "word " * 15)
for kind in ("concept", "script"):
    doc = services.create_document(post.pk, owner, kind=kind, markdown="## Opening hook\n\nA < B & café 🌱.\n\n- First line\n- Second line")
    version = doc.versions.get()
    services.name_version(version.pk, owner, "An exceptionally long version name " + "x" * 60)
    draft = services.edit(version.pk, owner)
    services.save(draft.pk, owner, expected_revision=draft.revision, markdown="## New hook\n\nFull text to copy.\n\n- First shot\n- Second shot")
variant = post.platform_posts.get()
first, _ = services.save_caption(post.pk, variant.pk, owner, "First caption 🌱")
services.name_version(first.pk, owner, "Original caption")
services.save_caption(post.pk, variant.pk, owner, "Caption < > & 🌱\nSecond line", base_version_id=first.pk)
shotlist.add_item(post, owner, text="Close-up", note="Window light")
shotlist.add_item(post, owner, text="Wide shot", note="Keep the whole room visible")
credentials.update({"detail": f"/jrc/workspace/{workspace.pk}/content/{post.pk}/", "category": str(category.pk)})
empty_post = create_content(workspace, "Empty content", owner, social_account_ids=[account.pk])
credentials["empty_detail"] = f"/jrc/workspace/{workspace.pk}/content/{empty_post.pk}/"
output = Path("output/p2b04-browser.env.local")
output.parent.mkdir(exist_ok=True)
output.write_text(json.dumps(credentials))
output.chmod(0o600)
print("Synthetic browser data ready.")
