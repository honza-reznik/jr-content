import json
import uuid
from pathlib import Path

from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.signals import provision_organization_and_workspace
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

org = Organization.objects.get(name="JR Content")
workspace = Workspace.objects.create(organization=org, name="P1-B23 role checks")
data = {"workspace": str(workspace.pk), "organization": str(org.pk), "sessions": {}, "personal": {}}
for i, role in enumerate(["owner", "manager", "editor", "contributor", "client", "viewer", "nonmember"]):
    user = User.objects.create_user(email=f"p1b23-{role}@example.invalid", tos_accepted_at=timezone.now())
    # Záměrně napodobit stará data; nové pozvané účty tuto funkci nevolají.
    provision_organization_and_workspace(user)
    personal = OrgMembership.objects.filter(user=user).first()
    if personal:
        personal.pk = uuid.UUID(int=100 + i)
        OrgMembership.objects.filter(user=user).delete()
        personal.save(force_insert=True)
        data["personal"][role] = str(personal.organization_id)
    if role != "nonmember":
        OrgMembership.objects.create(user=user, organization=org, org_role="owner" if role == "owner" else "member")
        WorkspaceMembership.objects.create(user=user, workspace=workspace, workspace_role=role)
        user.last_workspace_id = workspace.pk
        user.save(update_fields=["last_workspace_id"])
    else:
        if personal:
            personal.organization.delete()
    client = Client()
    client.force_login(user)
    data["sessions"][role] = client.cookies["sessionid"].value
p = Path("/app/.p1b23.env.local")
p.write_text(json.dumps(data))
p.chmod(0o600)
print("Testovací role připraveny; přihlašovací relace uloženy pouze do ignorovaného souboru.")
