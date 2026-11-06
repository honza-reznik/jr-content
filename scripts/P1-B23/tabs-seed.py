"""Disposable organizations for real two-tab regression; run only in own Docker project."""

import json
from pathlib import Path

from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

run = timezone.now().strftime("%H%M%S")  # fresh data on every run
data = {}
for mode in ("before", "after"):
    for width in (1440, 390):
        for action in (
            "update_name",
            "update_timezone",
            "delete_organization",
            "cancel_deletion",
            "delete_organization_now",
            "refused",  # A: org owner but only workspace viewer (refused writes)
        ):
            key = f"{mode}-{width}-{action}"
            user = User.objects.create_user(email=f"tabs-{run}-{key}@example.invalid", tos_accepted_at=timezone.now())
            pair = {}
            for side in ("a", "b"):
                org = Organization.objects.create(name=f"P1-B23 {key} {side}")
                if action in ("cancel_deletion", "delete_organization_now"):
                    org.deletion_requested_at = timezone.now()
                    org.deletion_scheduled_for = timezone.now() + timezone.timedelta(days=14)
                    org.save()
                workspace = Workspace.objects.create(organization=org, name=f"Tabs {side}")
                OrgMembership.objects.create(user=user, organization=org, org_role="owner")
                role = "viewer" if action == "refused" and side == "a" else "owner"
                WorkspaceMembership.objects.create(user=user, workspace=workspace, workspace_role=role)
                pair[side] = {"org": str(org.pk), "workspace": str(workspace.pk), "name": org.name}
            user.last_workspace_id = pair["a"]["workspace"]
            user.save(update_fields=["last_workspace_id"])
            client = Client()
            client.force_login(user)
            pair["session"] = client.cookies["sessionid"].value
            data[key] = pair
path = Path("/app/.p1b23-tabs.env.local")
path.write_text(json.dumps(data))
path.chmod(0o600)
print("Disposable A/B organizations and private sessions prepared.")
