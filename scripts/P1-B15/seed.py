"""Vymyšlená data pouze ve vlastním projektu P1-B15; spuštění přes manage.py shell."""

import json
import os
from io import BytesIO

from apps.accounts.models import User
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.services import create_content
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from PIL import Image, ImageDraw

assert os.environ.get("JRC_PUBLIC_ORIGIN") == "http://127.0.0.1:13573", (
    "Pouze jrc-p1b15!"
)
organization, _ = Organization.objects.get_or_create(name="P1-B15 test studio")
workspace, _ = Workspace.objects.get_or_create(
    organization=organization, name="Garden studio"
)
other, _ = Workspace.objects.get_or_create(
    organization=organization, name="Forest studio"
)
sessions, people = {}, {}
for role, name in (("owner", "Jamie"), ("client", "Ella")):
    person, _ = User.objects.get_or_create(
        email=f"{role}-p1b15@example.invalid",
        defaults={"name": name, "tos_accepted_at": timezone.now()},
    )
    OrgMembership.objects.get_or_create(
        organization=organization,
        user=person,
        defaults={"org_role": "owner" if role == "owner" else "member"},
    )
    WorkspaceMembership.objects.get_or_create(
        workspace=workspace, user=person, defaults={"workspace_role": role}
    )
    if role == "owner":
        WorkspaceMembership.objects.get_or_create(
            workspace=other, user=person, defaults={"workspace_role": role}
        )
    person.last_workspace_id = workspace.pk
    person.save(update_fields=["last_workspace_id"])
    http = Client()
    http.force_login(person)
    sessions[role], people[role] = http.cookies["sessionid"].value, person
accounts = []
for platform, name in (("instagram", "Garden Instagram"), ("custom", "Forendors")):
    account = workspace.social_accounts.filter(
        platform=platform, account_name=name
    ).first()
    accounts.append(account or create_manual_channel(workspace, platform, name))
post = workspace.posts.filter(title="Three autumn plants").first()
if not post:
    post = create_content(
        workspace,
        "Three autumn plants",
        people["owner"],
        social_account_ids=[a.pk for a in accounts],
    )
# A dated draft appears on both workspace and organization calendars.
post.proposed_publish_at = timezone.now()
post.save(update_fields=["proposed_publish_at"])
post.platform_posts.update(scheduled_at=post.proposed_publish_at)
other_post = other.posts.filter(title="Forest story").first()
if not other_post:
    create_content(other, "Forest story", people["owner"])
image = Image.new("RGB", (128, 128), "#6D28D9")
ImageDraw.Draw(image).text((24, 30), "F", fill="white", font_size=70)
buffer = BytesIO()
image.save(buffer, "PNG")
custom = accounts[1]
urls = {
    "profile": reverse("jrc_channels:profile", kwargs={"workspace_id": workspace.pk}),
    "colour": reverse("jrc_channels:color", kwargs={"workspace_id": workspace.pk}),
    "icon": reverse(
        "jrc_channels:icon",
        kwargs={"workspace_id": workspace.pk, "account_id": custom.pk},
    ),
    "board": reverse("jrc_production:board"),
    "detail": reverse(
        "jrc_production:content_detail",
        kwargs={"workspace_id": workspace.pk, "post_id": post.pk},
    ),
    "calendar": reverse("calendar:calendar", kwargs={"workspace_id": workspace.pk}),
    "cross_calendar": reverse("organizations:cross_workspace_calendar"),
}
# Session values are emitted only to an ignored file by the caller.
print(
    "SEED="
    + json.dumps(
        {
            "workspace": str(workspace.pk),
            "custom": str(custom.pk),
            "sessions": sessions,
            "urls": urls,
            "png": __import__("base64").b64encode(buffer.getvalue()).decode(),
        }
    )
)
