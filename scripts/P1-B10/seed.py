"""Vymyšlená data a obrázky pouze pro vlastní projekt jrc-p1b10."""

import json
import os
from pathlib import Path

from django.test import Client
from django.utils import timezone
from PIL import Image, ImageDraw

from apps.accounts.models import User
from apps.composer.models import Post
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.services import create_content
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

assert os.environ.get("JRC_PUBLIC_ORIGIN") == "http://127.0.0.1:13533", "Pouze vlastní projekt jrc-p1b10!"
organization, _ = Organization.objects.get_or_create(name="P1-B10 local studio")
workspace, _ = Workspace.objects.get_or_create(organization=organization, name="Garden test")
sessions, people = {}, {}
for role, name in (("owner", "Jamie"), ("client", "Ella")):
    person, _ = User.objects.get_or_create(
        email=f"{role}-p1b10@example.invalid", defaults={"name": name, "tos_accepted_at": timezone.now()}
    )
    OrgMembership.objects.get_or_create(
        organization=organization, user=person, defaults={"org_role": "owner" if role == "owner" else "member"}
    )
    WorkspaceMembership.objects.get_or_create(workspace=workspace, user=person, defaults={"workspace_role": role})
    person.last_workspace_id = workspace.pk
    person.save(update_fields=["last_workspace_id"])
    http = Client()
    http.force_login(person)
    sessions[role], people[role] = http.cookies["sessionid"].value, person
accounts = []
for platform, name in (("instagram", "Garden reels"), ("youtube", "Garden shorts")):
    account = workspace.social_accounts.filter(platform=platform, account_name=name).first()
    accounts.append(account or create_manual_channel(workspace, platform, name))
Post.objects.filter(workspace=workspace).delete()
posts = {}
for width in (1440, 390):
    post = create_content(workspace, "Three plants for autumn", people["owner"], social_account_ids=[a.pk for a in accounts])
    posts[str(width)] = {"id": str(post.pk), "variants": [str(post.platform_posts.get(social_account=a).pk) for a in accounts]}
root = Path("/tmp/p1b10-thumbnails")
root.mkdir(exist_ok=True)
for fmt, size, color, suffix in (("PNG", (960, 540), "#285643", "png"), ("JPEG", (540, 960), "#ba6634", "jpg"), ("WEBP", (960, 540), "#284c80", "webp")):
    image = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(image)
    draw.text((35, 40), f"GARDEN / {fmt}\nThree plants for autumn", fill="white", font_size=28)
    image.save(root / f"garden.{suffix}", format=fmt)
print("SEED=" + json.dumps({"workspace": str(workspace.pk), "posts": posts, "sessions": sessions}))
