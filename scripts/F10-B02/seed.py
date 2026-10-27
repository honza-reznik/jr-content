"""Testovací účty a klientka Ella pro celý průchod F10-B02; pouze jrc-f10b02."""

import json
import os
import secrets
from datetime import timedelta

from django.utils import timezone

from apps.accounts.models import User
from apps.api_keys.services import issue_api_key
from apps.jrc_channels.services import create_manual_channel
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

project = os.environ.get("JRC_PROJECT", "")
assert project.startswith("jrc-") and project != "jrc", "Pouze vlastní ověřovací projekt!"
assert project == os.environ.get("JRC_E2E_PROJECT", project), "Projekt E2E neodpovídá nastavenému projektu!"

organization = Organization.objects.create(name="F10-B02 Test Studio")
workspace = Workspace.objects.create(
    organization=organization,
    name="Ella",
    approval_workflow_mode="required_internal_and_client",
)


def create_person(key, name, role, org_role="member"):
    user = User.objects.create(
        email=f"{key}-{secrets.token_hex(4)}@f10-b02.example.invalid",
        name=name,
        tos_accepted_at=timezone.now(),
    )
    password = secrets.token_urlsafe(24)
    user.set_password(password)
    user.save(update_fields=["password"])
    OrgMembership.objects.create(organization=organization, user=user, org_role=org_role)
    WorkspaceMembership.objects.create(workspace=workspace, user=user, workspace_role=role)
    return {"email": user.email, "password": password}


admin = create_person("honza", "Honza Team", "owner", "owner")
ella = create_person("ella", "Ella", "client")
account = create_manual_channel(workspace, "instagram", "Ella Instagram")
issued = issue_api_key(
    workspace=workspace, social_accounts=[account], issued_by=User.objects.get(email=admin["email"]),
    name="P1-B01 local verification", permissions=[], expires_at=timezone.now() + timedelta(hours=1),
)

print(json.dumps({"workspace": str(workspace.pk), "admin": admin, "client": ella, "api_token": issued.plaintext_token}))
