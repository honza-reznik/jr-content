"""P1-B08: vymyšlená data a relace jen pro vlastní ověřovací projekt jrc-p1b08 (vypisuje JSON)."""

import json
import os

from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.composer.models import Post
from apps.jrc_production.services import create_content
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

assert os.environ.get("JRC_PUBLIC_ORIGIN", "").endswith(":13513"), "Pouze vlastní ověřovací projekt jrc-p1b08!"
organization, _ = Organization.objects.get_or_create(name="P1-B08 local studio")
workspace, _ = Workspace.objects.get_or_create(organization=organization, name="Ella test")
sessions = {}
people = {}
for key, name, role in (("owner", "Jamie", "owner"), ("client", "Ella", "client")):
    person, _ = User.objects.get_or_create(
        email=f"{key}-p1b08@example.invalid", defaults={"name": name, "tos_accepted_at": timezone.now()}
    )
    OrgMembership.objects.get_or_create(
        organization=organization, user=person, defaults={"org_role": "owner" if key == "owner" else "member"}
    )
    WorkspaceMembership.objects.get_or_create(workspace=workspace, user=person, defaults={"workspace_role": role})
    person.last_workspace_id = workspace.pk
    person.save(update_fields=["last_workspace_id"])
    http = Client()
    http.force_login(person)
    sessions[key] = http.cookies["sessionid"].value
    people[key] = person
Post.objects.filter(workspace=workspace).delete()
posts = {str(width): str(create_content(workspace, "Autumn plant care idea", people["owner"]).pk) for width in (1440, 390)}
print("SEED=" + json.dumps({"workspace": str(workspace.pk), "posts": posts, "sessions": sessions}))
