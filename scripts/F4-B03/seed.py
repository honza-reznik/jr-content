"""Vymyšlená data a dočasné relace jen v ověřovacím projektu F4-B03."""

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

assert os.environ.get("JRC_PROJECT") == "jrc-f4b03", "Pouze vlastní ověřovací projekt!"
organization, _ = Organization.objects.get_or_create(name="F4-B03 local studio")
workspace, _ = Workspace.objects.get_or_create(organization=organization, name="Ella")
sessions = {}
people = {}
for key, name, role in (("admin", "Jamie", "owner"), ("client", "Ella", "client")):
    person, _ = User.objects.get_or_create(
        email=f"{key}-f4b03@example.invalid", defaults={"name": name, "tos_accepted_at": timezone.now()}
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
Post.objects.filter(workspace=workspace).delete()
post = create_content(workspace, "A morning in the studio", people["admin"])
document = services.create_document(
    post.pk,
    people["admin"],
    markdown="A morning in the studio\n\nLet the light in.\n\n1. Open the studio\n2. Make a cup of tea\n\n- [ ] Film the opening shot\n\nA quiet moment before the day begins.",
)
for markdown in (
    "## A morning in the studio\n\nLet the warm light in.\n\n1. Open the studio\n2. Make a cup of tea\n\n- [ ] Film the opening shot",
    "## A morning in the studio\n\nLet the **warm** light in.\n\n5. Open the studio\n6. Make a cup of tea\n\n- [x] Film the opening shot\n\nInvite viewers behind the scenes.",
):
    draft = services.edit(services.latest_version(document.pk, people["admin"]).pk, people["admin"])
    services.save(draft.pk, people["admin"], expected_revision=0, markdown=markdown)
Path("/app/media/f4b03-session.json").write_text(
    json.dumps({"workspace": str(workspace.pk), "post": str(post.pk), "sessions": sessions})
)
print("Místní verze 1–3 a relace F4-B03 připraveny; žádné skutečné účty.")
