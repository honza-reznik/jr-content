"""Fiktivní člen a workspace jen pro postradatelný projekt jrc-f2b05; spouštět v Dockeru.

Výstup (JSON s relací) jde na stdout; ukládá se jen do ignorovaného ops/test-output.
"""

import json
import os

from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

if os.environ.get("JRC_PROJECT") != "jrc-f2b05":
    raise RuntimeError("Tato data patří jen do jrc-f2b05.")

org, _ = Organization.objects.get_or_create(name="F2-B05 Demo Studio")
workspace, _ = Workspace.objects.get_or_create(organization=org, name="Demo Client S3")
user, _ = User.objects.get_or_create(
    email="f2b05-member@example.invalid",
    defaults={"name": "Demo member", "tos_accepted_at": timezone.now()},
)
OrgMembership.objects.get_or_create(user=user, organization=org, defaults={"org_role": "owner"})
WorkspaceMembership.objects.get_or_create(user=user, workspace=workspace, defaults={"workspace_role": "owner"})
user.last_workspace_id = workspace.id
user.save(update_fields=["last_workspace_id"])
client = Client()
client.force_login(user)
print("JSON:" + json.dumps({"workspace": str(workspace.id), "session": client.cookies["sessionid"].value}))
