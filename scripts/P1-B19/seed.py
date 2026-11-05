"""Vymyšlená data pouze pro jrc-p1b19; výstup se ukládá mimo Git."""

import base64
import json
import os
import uuid
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone
from PIL import Image

from apps.accounts.models import User
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.services import create_content, move_content
from apps.jrc_thumbnails import services as thumbnails
from apps.jrc_video.services import sync_workspace
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

assert os.environ.get("JRC_PUBLIC_ORIGIN") == "http://127.0.0.1:13623"
organization = Organization.objects.create(name="P1-B19 local studio")
workspace = Workspace.objects.create(
    organization=organization, name="P1-B19 Garden", approval_workflow_mode="required_internal"
)
sessions, people = {}, {}
suffix = uuid.uuid4().hex[:8]
for role in ("owner", "client"):
    person = User.objects.create(
        email=f"{role}-p1b19-{suffix}@example.invalid", name=f"Garden {role}", tos_accepted_at=timezone.now()
    )
    OrgMembership.objects.create(
        organization=organization, user=person, org_role="owner" if role == "owner" else "member"
    )
    WorkspaceMembership.objects.create(workspace=workspace, user=person, workspace_role=role)
    person.last_workspace_id = workspace.pk
    person.save(update_fields=["last_workspace_id"])
    http = Client()
    http.force_login(person)
    sessions[role], people[role] = http.cookies["sessionid"].value, person
account = create_manual_channel(workspace, "instagram", "Garden reels")
image = BytesIO()
Image.new("RGB", (320, 180), "#6D28D9").save(image, "PNG")
posts = {}
for width in (1440, 390):
    post = create_content(
        workspace, f"Garden feedback {width}", people["owner"], social_account_ids=[account.pk]
    )
    variant = post.platform_posts.get()
    variant.platform_specific_caption = "Three plants for autumn."
    variant.save(update_fields=["platform_specific_caption"])
    thumbnails.upload_thumbnail(
        post, variant, people["owner"],
        SimpleUploadedFile("garden.png", image.getvalue(), content_type="image/png"),
        thumbnails.member(workspace, people["owner"]),
    )
    move_content(post, "review", people["owner"], expected_stage="idea")
    posts[str(width)] = {"id": str(post.pk), "variant": str(variant.pk), "title": post.title}
sync_workspace(workspace.pk)
print("SEED=" + json.dumps({
    "workspace": str(workspace.pk), "posts": posts, "sessions": sessions,
    "image": base64.b64encode(image.getvalue()).decode(),
}))
