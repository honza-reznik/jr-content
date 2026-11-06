"""Živé ověření výpisu, čištění a přesného návratu na kopii zálohy P1-B23."""

import json
import os
from pathlib import Path

from django.core import serializers
from django.core.management import call_command
from django.test import override_settings

from apps.accounts.models import User
from apps.accounts.signals import provision_organization_and_workspace
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

assert os.environ["JRC_PUBLIC_ORIGIN"] == "http://127.0.0.1:13653"
data = json.loads(Path("/app/.p1b23.env.local").read_text())
for signup in data["signups"]:
    user = User.objects.get(email=signup["email"])
    assert list(user.org_memberships.values_list("organization_id", flat=True)) == [
        Organization.objects.get(pk=data["organization"]).pk
    ]
    assert list(user.workspace_memberships.values_list("workspace_id", "workspace_role")) == [
        (Workspace.objects.get(pk=data["workspace"]).pk, signup["role"])
    ]
print("Oba registrované účty mají právě jednu týmovou organizaci a přidělený workspace.")

# Propojení FreeFrame je záměrně chráněná vazba. Připravit také prokazatelně
# prázdnou starou organizaci bez něj a chybějící týmové členství.
with override_settings(JRC_FF_SYNC_ENABLED=False):
    legacy = User.objects.create_user(email="p1b23-repair-check@example.invalid")
    provision_organization_and_workspace(legacy)
    WorkspaceMembership.objects.create(user=legacy, workspace_id=data["workspace"], workspace_role="editor")


def snapshot():
    rows = [
        row
        for model in [Organization, Workspace, OrgMembership, WorkspaceMembership]
        for row in model.objects.order_by("pk")
    ]
    return serializers.serialize("json", rows), list(User.objects.order_by("pk").values_list("pk", "last_workspace_id"))


before = snapshot()
call_command("jrc_repair_organizations")
assert snapshot() == before
journal = Path("/app/.p1b23-repair-final.env.local")
call_command("jrc_repair_organizations", apply=True, backup_file=journal)
payload = json.loads(journal.read_text())
assert payload["removed"], "Kopie zálohy musí obsahovat připravené prázdné osobní organizace."
assert journal.stat().st_mode & 0o777 == 0o600
print(
    "Odstraněné organizace:", len([row for row in payload["removed"] if row["model"] == "organizations.organization"])
)
call_command("jrc_repair_organizations", restore=journal)
assert snapshot() == before
print(
    "Výpis nic nezměnil; po čištění a návratu se shodují všechny organizace, workspacy, členství i ukazatele uživatelů."
)
