"""Vymyšlená data a dočasné relace výhradně vlastního Docker projektu."""

import json
import os
from pathlib import Path

from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.composer.models import Post
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.services import create_content
from apps.jrc_texts import services
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

assert os.environ.get("JRC_PROJECT") == "jrc-f4b02", "Pouze vlastní ověřovací projekt!"
organization, _ = Organization.objects.get_or_create(name="F4-B02 local studio")
workspace, _ = Workspace.objects.get_or_create(organization=organization, name="Ella")
sessions = {}
people = {}
for key, name, role in (("admin", "Jamie", "owner"), ("editor", "Alex", "editor"), ("client", "Ella", "client")):
    person, _ = User.objects.get_or_create(
        email=f"{key}-f4b02@example.invalid", defaults={"name": name, "tos_accepted_at": timezone.now()}
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
# Opakování maže pouze vlastní testovací obsah; celou historii lze odstranit s příspěvkem.
Post.objects.filter(workspace=workspace).delete()
posts = {}
for width in (1440, 390):
    post = create_content(workspace, "A morning in the studio", people["admin"])
    document = services.create_document(
        post.pk,
        people["admin"],
        markdown="# A slower start\n\nShare the small rituals behind the autumn collection.\n\n- Open the studio\n- Make a cup of tea\n\n- [ ] Film the opening shot\n- [x] Prepare the props",
    )
    draft = services.edit(services.latest_version(document.pk, people["admin"]).pk, people["admin"])
    services.save(
        draft.pk,
        people["admin"],
        expected_revision=0,
        markdown="## A morning in the studio\n\nA quiet moment before the day begins.\n\n**Opening:** Let the light in and take a breath.\n\n1. Welcome to the studio\n2. Show the autumn collection\n3. Invite viewers behind the scenes\n\n- [x] Prepare the props\n- [ ] Film the opening shot\n\n*Keep the pacing warm and natural.*",
    )
    posts[str(width)] = str(post.pk)
empty = create_content(workspace, "A new story", people["admin"])
Path("/app/media/f4b02-session.json").write_text(
    json.dumps({"workspace": str(workspace.pk), "posts": posts, "empty": str(empty.pk), "sessions": sessions})
)
print("Místní data a dočasné relace F4-B02 připraveny; žádné skutečné účty.")
