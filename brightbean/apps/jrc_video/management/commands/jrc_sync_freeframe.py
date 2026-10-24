from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.jrc_video.client import FreeFrameClient, FreeFrameError
from apps.jrc_video.models import VideoAccount, VideoAccountRevocation, VideoAccountSyncRequest, VideoSyncRequest
from apps.jrc_video.services import error_text, mark_account_failed, revoke_account, sync_account, sync_workspace
from apps.workspaces.models import Workspace


class Command(BaseCommand):
    help = (
        "Synchronize local accounts, client projects and memberships with FreeFrame. "
        "A full run also removes FreeFrame project members added by hand outside JR Content."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--pending", action="store_true", help="Retry only accounts/workspaces with pending changes."
        )

    def handle(self, *args, **options):
        api = FreeFrameClient()
        failures = 0
        # Deletions have priority and no user FK; even a full run must retry them.
        for user_id in list(VideoAccountRevocation.objects.values_list("source_user_id", flat=True)):
            try:
                revoke_account(user_id, api)
            except FreeFrameError as exc:
                failures += 1
                VideoAccountRevocation.objects.filter(source_user_id=user_id).update(last_error=error_text(exc))
                self.stderr.write(str(exc))
        user_ids = VideoAccount.objects.values_list("user_id", flat=True)
        if options["pending"]:
            user_ids = VideoAccountSyncRequest.objects.values_list("user_id", flat=True)
        for user_id in list(user_ids.order_by("pk")):
            try:
                sync_account(user_id, api)
            except get_user_model().DoesNotExist:
                continue
            except FreeFrameError as exc:
                failures += 1
                mark_account_failed(user_id, exc)
                self.stderr.write(str(exc))
        workspaces = Workspace.objects.order_by("pk")
        if options["pending"]:
            workspaces = workspaces.filter(pk__in=VideoSyncRequest.objects.values("workspace_id"))
        count = 0
        for workspace in workspaces:
            VideoSyncRequest.objects.get_or_create(workspace=workspace)
            try:
                sync_workspace(workspace.pk, api=api)
                count += 1
            except FreeFrameError as exc:
                failures += 1
                VideoSyncRequest.objects.filter(workspace=workspace).update(last_error=error_text(exc))
                self.stderr.write(str(exc))
        if failures:
            raise CommandError(f"FreeFrame synchronization failed ({failures} errors); retry after fixing the API.")
        self.stdout.write(self.style.SUCCESS(f"FreeFrame synchronized: {count} workspaces."))
