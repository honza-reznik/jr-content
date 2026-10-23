"""Fiktivní data jen pro postradatelný projekt jrc-f2b03; spouštět v Dockeru."""

import io
import json
import os
from pathlib import Path

from django.core.files.base import ContentFile
from django.test import Client
from django.utils import timezone
from PIL import Image, ImageDraw

from apps.accounts.models import User
from apps.media_library.models import MediaAsset, MediaAssetVersion
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

if os.environ.get("JRC_PROJECT") != "jrc-f2b03":
    raise RuntimeError("Tato data patří jen do jrc-f2b03.")

org, _ = Organization.objects.get_or_create(name="F2-B03 Demo Studio")
workspace, _ = Workspace.objects.get_or_create(organization=org, name="Demo Client A")
other_workspace, _ = Workspace.objects.get_or_create(organization=org, name="Demo Client B")
sessions = {}
for name, target in (("member", workspace), ("outsider", other_workspace)):
    user, _ = User.objects.get_or_create(
        email=f"f2b03-{name}@example.invalid",
        defaults={"name": f"Demo {name}", "tos_accepted_at": timezone.now()},
    )
    OrgMembership.objects.get_or_create(user=user, organization=org)
    WorkspaceMembership.objects.get_or_create(user=user, workspace=target)
    user.last_workspace_id = target.id
    user.save(update_fields=["last_workspace_id"])
    client = Client()
    client.force_login(user)
    sessions[name] = client.cookies["sessionid"].value

image = Image.new("RGB", (640, 400), "#1d5555")
draw = ImageDraw.Draw(image)
draw.rectangle((60, 60, 580, 340), fill="#e7cda1")
draw.text((100, 150), "JR Content / Demo client", fill="#1d5555", font_size=28)
stream = io.BytesIO()
image.save(stream, format="PNG")
payload = stream.getvalue()
paths = []
for label in ("Original preview", "Thumbnail preview"):
    asset, created = MediaAsset.objects.get_or_create(
        workspace=workspace,
        filename=f"{label}.png",
        defaults={
            "organization": org,
            "media_type": "image",
            "mime_type": "image/png",
            "width": 640,
            "height": 400,
            "file_size": len(payload),
        },
    )
    if created:
        asset.file.save("demo.png", ContentFile(payload), save=False)
        if label.startswith("Thumbnail"):
            asset.thumbnail.save("demo-thumb.png", ContentFile(payload), save=False)
        asset.save()
        version = MediaAssetVersion.objects.create(media_asset=asset, version_number=1)
        version.file.save("demo-v1.png", ContentFile(payload), save=False)
        version.thumbnail.save("demo-v1-thumb.png", ContentFile(payload), save=True)
    paths.append(asset.file.url)
    if asset.thumbnail:
        paths.append(asset.thumbnail.url)
    version = asset.versions.get(version_number=1)
    paths.extend((version.file.url, version.thumbnail.url))

# Přihlášení zůstávají v postradatelném Docker svazku, nikdy v gitu ani KB.
Path("/app/media/f2b03-media-check.json").write_text(
    json.dumps({"workspace": str(workspace.id), "sessions": sessions, "paths": paths})
)
print("Připraveny dva fiktivní workspace, dva členové a média pro živé ověření.")
