"""Vymyšlená data a dočasné relace jen v ověřovacím projektu F6-B02.

Spouští se v kontejneru Brightbean: `manage.py shell < seed.py`. Výstup je JSON
s identifikátory a relacemi, uloží se do `ops/test-output/F6-B02/session.json`.
"""

import json
import os

from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.composer.models import Post
from apps.jrc_channels.services import create_manual_channel, set_channel_enabled
from apps.jrc_production.services import create_content
from apps.jrc_texts.models import TextDocument
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace
from apps.composer.models import PlatformPost

assert os.environ.get("JRC_PROJECT") == "jrc-f6b02", "Pouze vlastní ověřovací projekt!"
organization, _ = Organization.objects.get_or_create(name="F6-B02 local studio")
workspace, _ = Workspace.objects.get_or_create(organization=organization, name="Ella")
sessions = {}
people = {}
for key, name, role in (("admin", "Jamie", "owner"), ("client", "Ella", "client")):
    person, _ = User.objects.get_or_create(
        email=f"{key}-f6b02@example.invalid", defaults={"name": name, "tos_accepted_at": timezone.now()}
    )
    OrgMembership.objects.get_or_create(
        organization=organization, user=person, defaults={"org_role": "owner" if key == "admin" else "member"}
    )
    WorkspaceMembership.objects.get_or_create(workspace=workspace, user=person, defaults={"workspace_role": role})
    person.last_workspace_id = workspace.pk
    person.save(update_fields=["last_workspace_id"])
    http = Client()
    http.force_login(person)
    sessions[key] = http.cookies["sessionid"].value
    people[key] = person
if not workspace.social_accounts.exists():
    create_manual_channel(workspace, "instagram", "Ella Instagram")
    create_manual_channel(workspace, "youtube", "Ella YouTube")
Post.objects.filter(workspace=workspace).delete()
post = create_content(workspace, "A morning in the studio", people["admin"])
# Kanál zapnutý až po založení obsahu (pole je připravené) a kanál vypnutý (skrytý).
workspace.social_accounts.filter(platform__in=["linkedin_personal", "tiktok"]).delete()
later = create_manual_channel(workspace, "linkedin_personal", "Ella LinkedIn")
hidden = create_manual_channel(workspace, "tiktok", "Ella TikTok")
set_channel_enabled(hidden, enabled=False)

client = Client(HTTP_HOST="jrc-bb-web:8000")
client.force_login(people["admin"])
variant = PlatformPost.objects.get(post=post, social_account__platform="instagram")
detail = f"/workspace/{workspace.pk}/content/{post.pk}/?tab=caption"
caption = (
    "Morning light in the studio \U0001f331\n\n"
    "Slow coffee, open windows and a fresh set of ideas. Here is what we are filming today:\n"
    "• the warm-up shots\n• a quick tour\n• the big reveal\n\n"
    "Save this for your own slow mornings ☕️ #studio #morning"
)
for text, base in (("First draft of the caption", ""), (caption, "last")):
    document = TextDocument.objects.filter(platform_post=variant, kind="caption").first()
    base_id = document.versions.filter(state="final").order_by("-number").first().pk if base and document else ""
    response = client.post(
        detail, {"action": "save_caption", "platform_post": str(variant.pk), "base_version": str(base_id), "caption": text}
    )
    assert response.status_code == 302, response.status_code
print(
    "SESSION_JSON="
    + json.dumps({"workspace": str(workspace.pk), "post": str(post.pk), "sessions": sessions, "caption": caption})
)
