"""Testovací účty a klientka Ella pro celý průchod F10-B02; pouze jrc-f10b02."""

import json
import os
import secrets

from django.utils import timezone

from apps.accounts.models import User
from apps.jrc_channels.services import create_manual_channel
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

assert os.environ.get("JRC_PROJECT") == "jrc-f10b02", "Pouze vlastní ověřovací projekt!"

organization = Organization.objects.create(name="F10-B02 Test Studio")
workspace = Workspace.objects.create(
    organization=organization,
    name="Ella",
    approval_workflow_mode="required_internal_and_client",
)


def create_person(key, name, role, org_role="member"):
    user = User.objects.create(
        email=f"{key}@f10-b02.example.invalid",
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
create_manual_channel(workspace, "instagram", "Ella Instagram")

print(json.dumps({"workspace": str(workspace.pk), "admin": admin, "client": ella}))
