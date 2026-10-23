from django.core.management.base import BaseCommand, CommandError

from apps.jrc_video.client import FreeFrameClient, FreeFrameError
from apps.jrc_video.models import VideoAccount, VideoSyncRequest
from apps.jrc_video.services import sync_account, sync_workspace
from apps.workspaces.models import Workspace


class Command(BaseCommand):
    help = "Synchronize local accounts, client projects and memberships with FreeFrame."

    def add_arguments(self, parser):
        parser.add_argument("--pending", action="store_true", help="Retry only workspaces with pending changes.")

    def handle(self, *args, **options):
        api = FreeFrameClient()
        failures = 0
        if not options["pending"]:
            for user_id in VideoAccount.objects.values_list("user_id", flat=True):
                try:
                    sync_account(user_id, api)
                except FreeFrameError as exc:
                    failures += 1
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
                VideoSyncRequest.objects.filter(workspace=workspace).update(last_error=str(exc))
                self.stderr.write(str(exc))
        if failures:
            raise CommandError(f"FreeFrame synchronization failed ({failures} errors); retry after fixing the API.")
        self.stdout.write(self.style.SUCCESS(f"FreeFrame synchronized: {count} workspaces."))
