"""Explicit, journalled legacy-data repair; never run by migrate or web requests."""

import json
import os
from pathlib import Path

from django.core import serializers
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import User
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

ALLOWED_MODELS = {
    "organizations.organization",
    "workspaces.workspace",
    "members.orgmembership",
    "members.workspacemembership",
}


def has_other_relations(obj, allowed):
    """Refuse *any* related data, including nullable relations a cascade would clear."""
    for relation in obj._meta.related_objects:
        if relation.related_model in allowed:
            continue
        if relation.related_model._base_manager.filter(**{relation.field.name: obj.pk}).exists():
            return True
    return False


def empty_personal_rows(org):
    if (
        org.name != "My Organization"
        or org.logo_url
        or org.billing_email
        or org.default_timezone != "UTC"
        or org.deletion_requested_at
        or org.deletion_scheduled_for
        or org.deleted_at
    ):
        return None
    members = list(org.memberships.select_related("user"))
    if len(members) != 1 or members[0].org_role != "owner":
        return None
    member = members[0]
    # Conservative: keep the user's only org, and ambiguous default-name orgs.
    if not (
        OrgMembership.objects.filter(user=member.user)
        .exclude(organization=org)
        .exclude(organization__name="My Organization")
        .filter(organization__deleted_at__isnull=True)
        .exists()
        or WorkspaceMembership.objects.filter(user=member.user)
        .exclude(workspace__organization=org)
        .exclude(workspace__organization__name="My Organization")
        .filter(workspace__organization__deleted_at__isnull=True)
        .exists()
    ):
        return None
    workspaces = list(org.workspaces.all())
    if len(workspaces) != 1:
        return None
    workspace = workspaces[0]
    if (
        workspace.name != "My Workspace"
        or workspace.description != "Your default workspace. Rename it anytime."
        or workspace.icon
        or workspace.timezone
        or workspace.primary_color
        or workspace.secondary_color
        or workspace.default_hashtags
        or workspace.default_first_comment
        or workspace.is_archived
        or workspace.approval_workflow_mode != "none"
    ):
        return None
    ws_members = list(workspace.memberships.all())
    if (
        len(ws_members) != 1
        or ws_members[0].user_id != member.user_id
        or ws_members[0].workspace_role != "owner"
        or ws_members[0].custom_role_id
    ):
        return None
    if has_other_relations(org, {OrgMembership, Workspace}) or has_other_relations(workspace, {WorkspaceMembership}):
        return None
    return [org, workspace, member, ws_members[0]]


def write_journal(path, payload):
    # Exclusive create, private permissions, flushed before the first deletion.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(payload, stream)
        stream.flush()
        os.fsync(stream.fileno())
    # Persist the newly created directory entry too, before committing deletions.
    directory_fd = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


class Command(BaseCommand):
    help = "Dry-run by default. Repair missing org membership and remove only untouched empty personal orgs, with undo journal."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--backup-file", type=Path)
        parser.add_argument("--restore", type=Path)

    def handle(self, *args, **options):
        if options["restore"]:
            if options["apply"] or options["backup_file"]:
                raise CommandError("--restore nelze kombinovat s --apply/--backup-file.")
            return self.restore(options["restore"])
        if options["apply"] and not options["backup_file"]:
            raise CommandError("Zápis vyžaduje --backup-file (nový soubor mimo git, po úplné záloze).")
        with transaction.atomic():
            # Lock parents before checking emptiness: concurrent FK inserts must wait.
            orgs = list(Organization.objects.select_for_update().order_by("pk"))
            list(Workspace.objects.select_for_update().order_by("pk"))
            list(User.objects.select_for_update().order_by("pk"))
            list(OrgMembership.objects.select_for_update().order_by("pk"))
            list(WorkspaceMembership.objects.select_for_update().order_by("pk"))
            candidates = [rows for org in orgs if (rows := empty_personal_rows(org))]
            candidate_ids = {rows[0].pk for rows in candidates}
            for org in orgs:
                if org.name == "My Organization" and org.pk not in candidate_ids:
                    self.stdout.write(
                        f"PONECHAT OSOBNÍ ORGANIZACI {org.pk}: obsah, úpravy, další vazby "
                        "(včetně propojení FreeFrame), nebo nejednoznačné vlastnictví."
                    )
            missing = []
            for user_id, org_id in (
                WorkspaceMembership.objects.filter(workspace__organization__deleted_at__isnull=True)
                .values_list("user_id", "workspace__organization_id")
                .distinct()
            ):
                if not OrgMembership.objects.filter(user_id=user_id, organization_id=org_id).exists():
                    missing.append(OrgMembership(user_id=user_id, organization_id=org_id, org_role="member"))
            for rows in candidates:
                self.stdout.write(
                    f"PRÁZDNÁ OSOBNÍ ORGANIZACE {rows[0].pk}, workspace {rows[1].pk}, uživatel {rows[2].user_id}"
                )
            for member in missing:
                self.stdout.write(f"DOPLNIT MEMBER uživatel {member.user_id}, organizace {member.organization_id}")
            self.stdout.write(f"Celkem: {len(candidates)} prázdných organizací, {len(missing)} chybějících členství.")
            if not options["apply"]:
                self.stdout.write("Pouze výpis; žádná data změněna.")
                return
            removed_ws_ids = [rows[1].pk for rows in candidates]
            pointers = [
                {"user": str(u.pk), "workspace": str(u.last_workspace_id)}
                for u in User.objects.filter(last_workspace_id__in=removed_ws_ids)
            ]
            # Build the journal without saving new members (auto timestamps are filled on save).
            payload = {
                "version": 1,
                "removed": json.loads(serializers.serialize("json", [obj for rows in candidates for obj in rows])),
                "added": [
                    {"id": str(m.pk), "user": str(m.user_id), "organization": str(m.organization_id)} for m in missing
                ],
                "pointers": pointers,
            }
            try:
                write_journal(options["backup_file"], payload)
            except OSError as exc:
                raise CommandError(f"Zálohu nelze bezpečně vytvořit: {exc}") from exc
            for member in missing:
                member.save(force_insert=True)
            User.objects.filter(last_workspace_id__in=removed_ws_ids).update(last_workspace_id=None)
            for rows in candidates:
                rows[0].delete()
            self.stdout.write("Hotovo. Návrat: jrc_repair_organizations --restore <stejný soubor>.")

    @transaction.atomic
    def restore(self, path):
        try:
            payload = json.loads(path.read_text())
            if payload["version"] != 1:
                raise ValueError("Neznámá verze")
            if any(row["model"] not in ALLOWED_MODELS for row in payload["removed"]):
                raise ValueError("Nepovolený model")
            objects = list(serializers.deserialize("json", json.dumps(payload["removed"])))
        except (OSError, ValueError, KeyError) as exc:
            raise CommandError(f"Neplatný soubor návratu: {exc}") from exc
        # Never overwrite records or undo a membership that was subsequently promoted.
        for item in objects:
            if type(item.object).objects.filter(pk=item.object.pk).exists():
                raise CommandError("Konflikt: obnovovaný záznam již existuje. Nic nezměněno.")
        added = []
        for row in payload["added"]:
            member = (
                OrgMembership.objects.select_for_update()
                .filter(
                    pk=row["id"],
                    user_id=row["user"],
                    organization_id=row["organization"],
                    org_role="member",
                )
                .first()
            )
            if not member:
                raise CommandError("Konflikt: doplněné členství bylo změněno. Nic nezměněno.")
            added.append(member)
        for item in objects:
            item.save()  # raw deserialization preserves original UUIDs/timestamps, without provisioning.
        for member in added:
            member.delete()
        for pointer in payload["pointers"]:
            User.objects.filter(pk=pointer["user"], last_workspace_id=None).update(
                last_workspace_id=pointer["workspace"]
            )
        self.stdout.write("Původní organizace a členství obnoveny; novější volba workspace zachována.")
