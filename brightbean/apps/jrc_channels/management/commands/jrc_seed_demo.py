"""Fictitious local data; never logs in to a social network or resets existing content."""

from uuid import UUID

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.members.models import OrgMembership, WorkspaceMembership
from apps.workspaces.models import Workspace

from ...services import create_manual_channel

DEMO_USER_ID = UUID("f2080000-0000-4000-8000-000000000001")
DEMO_WORKSPACE_ID = UUID("f2080000-0000-4000-8000-000000000002")
DEMO_CHANNEL_IDS = {
    "instagram": "jrc-demo:ella:instagram",
    "custom": "jrc-demo:ella:forendors",
}


class Command(BaseCommand):
    help = "Create Ella's fictitious demo workspace with Instagram and Forendors. Safe to run again."

    def add_arguments(self, parser):
        parser.add_argument("--owner-email", help="Give an existing local user access to the demo workspace.")

    @transaction.atomic
    def handle(self, *args, **options):
        user_model = get_user_model()
        owner = None
        if options["owner_email"]:
            owner = user_model.objects.filter(email__iexact=options["owner_email"], is_active=True).first()
            if owner is None:
                raise CommandError("No active local user has that email. No demo data was changed.")

        # Deterministic IDs distinguish seeded data from a client's own workspace or account.
        user = user_model.objects.filter(pk=DEMO_USER_ID).first()
        if user is None:
            if user_model.objects.filter(email="ella-demo@example.invalid").exists():
                raise CommandError("The demo email is already in use. Existing data was left untouched.")
            user = user_model.objects.create_user(
                id=DEMO_USER_ID,
                email="ella-demo@example.invalid",
                name="Ella (demo)",
                tos_accepted_at=timezone.now(),
            )
            # Never create a shared demo password.
            user.set_unusable_password()
            user.save(update_fields=["password"])

        workspace = Workspace.objects.filter(pk=DEMO_WORKSPACE_ID).first()
        if workspace is None:
            org_membership = OrgMembership.objects.filter(user=user).select_related("organization").first()
            if org_membership is None:
                raise CommandError("The demo user's organization is missing. Existing data was left untouched.")
            workspace = Workspace.objects.create(
                id=DEMO_WORKSPACE_ID,
                organization=org_membership.organization,
                name="Ella",
                description="Fictitious demo client. All profile links use example.invalid.",
            )
        WorkspaceMembership.objects.get_or_create(user=user, workspace=workspace, defaults={"workspace_role": "owner"})
        if owner:
            OrgMembership.objects.get_or_create(
                user=owner, organization=workspace.organization, defaults={"org_role": "member"}
            )
            WorkspaceMembership.objects.get_or_create(
                user=owner, workspace=workspace, defaults={"workspace_role": "owner"}
            )

        for platform, name, link in [
            ("instagram", "Ella · Instagram", "https://example.invalid/ella/instagram"),
            ("custom", "Forendors", "https://example.invalid/ella/forendors"),
        ]:
            seed_id = DEMO_CHANNEL_IDS[platform]
            # Stable IDs preserve names, links and disabled state on rerun.
            if not workspace.social_accounts.filter(account_platform_id=seed_id).exists():
                account = create_manual_channel(workspace, platform, name, display_name=name, profile_url=link)
                account.account_platform_id = seed_id
                account.save(update_fields=["account_platform_id", "updated_at"])

        self.stdout.write(
            self.style.SUCCESS("Ella demo is ready. Existing names, links, channel states and posts are kept.")
        )
        self.stdout.write(reverse("jrc_channels:profile", kwargs={"workspace_id": workspace.pk}))
        self.stdout.write("Demo links are fictitious. The demo account has no login password.")
        if owner is None:
            self.stdout.write("To open it with your local account, run again with --owner-email YOUR_LOCAL_EMAIL.")
