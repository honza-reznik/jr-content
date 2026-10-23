import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.members.models import WorkspaceMembership
from apps.workspaces.models import Workspace

from .client import FreeFrameClient, FreeFrameError
from .models import VideoAccount, VideoProject, VideoSyncRequest

logger = logging.getLogger(__name__)
ROLE_MAP = {
    "owner": "editor",
    "manager": "editor",
    "editor": "editor",
    "contributor": "editor",
    "client": "reviewer",
    "viewer": "viewer",
}


def sync_account(user_id, api):
    # Serializes requests for a user who belongs to several workspaces.
    with transaction.atomic():
        user = get_user_model().objects.select_for_update(no_key=True).get(pk=user_id)
        remote = api.request(
            "PUT",
            f"/jrc-sync/accounts/{user.pk}",
            {"email": user.email, "name": user.display_name, "is_active": user.is_active},
        )
        account, _ = VideoAccount.objects.update_or_create(
            user=user, defaults={"ff_user_id": remote["id"], "synced_at": timezone.now()}
        )
        return account


def sync_workspace(workspace_id, *, api=None):
    api = api or FreeFrameClient()
    with transaction.atomic():
        workspace = Workspace.objects.select_for_update(no_key=True).get(pk=workspace_id)
        requested_at = (
            VideoSyncRequest.objects.filter(workspace=workspace).values_list("requested_at", flat=True).first()
        )
        remote = api.request("PUT", f"/jrc-sync/projects/{workspace.pk}", {"name": workspace.name})
        project_id = remote["id"]
        members = api.request("GET", f"/projects/{project_id}/members")
        existing = {member["user_id"]: member["role"] for member in members}
        desired = {}
        for membership in (
            WorkspaceMembership.objects.filter(workspace=workspace).select_related("user").order_by("user_id")
        ):
            try:
                account = sync_account(membership.user_id, api)
            except get_user_model().DoesNotExist:
                # A concurrent user deletion already revoked the remote account.
                continue
            if account.user.is_active and not workspace.is_archived:
                # Unknown/custom roles must not accidentally grant upload permission.
                desired[str(account.ff_user_id)] = (
                    "viewer" if membership.custom_role_id else ROLE_MAP.get(membership.workspace_role, "viewer")
                )
        service_id = remote["service_user_id"]
        for user_id, role in desired.items():
            if user_id == service_id:
                raise FreeFrameError("The service account cannot be a workspace member.")
            if user_id not in existing:
                api.request("POST", f"/projects/{project_id}/members", {"user_id": user_id, "role": role})
            elif existing[user_id] != role:
                api.request("PATCH", f"/projects/{project_id}/members/{user_id}", {"role": role})
        for user_id in existing.keys() - desired.keys() - {service_id}:
            api.request("DELETE", f"/projects/{project_id}/members/{user_id}")
        project, _ = VideoProject.objects.update_or_create(
            workspace=workspace, defaults={"ff_project_id": project_id, "synced_at": timezone.now()}
        )
        # Preserve a newer change queued while the remote API was in flight.
        VideoSyncRequest.objects.filter(workspace=workspace, requested_at=requested_at).delete()
        return project


def try_sync_workspace(workspace_id):
    try:
        sync_workspace(workspace_id)
    except Workspace.DoesNotExist:
        return
    except FreeFrameError as exc:
        VideoSyncRequest.objects.filter(workspace_id=workspace_id).update(last_error=str(exc))
        logger.error("FreeFrame synchronization failed for workspace %s: %s", workspace_id, exc)


def try_sync_account(user_id):
    """Account without memberships (e.g. blocked); the full command retries it."""
    try:
        sync_account(user_id, FreeFrameClient())
    except get_user_model().DoesNotExist:
        return
    except FreeFrameError as exc:
        logger.error("FreeFrame account synchronization failed for user %s: %s; run jrc_sync_freeframe.", user_id, exc)


def queue_workspace(workspace_id, *, using="default"):
    VideoSyncRequest.objects.using(using).update_or_create(workspace_id=workspace_id, defaults={"last_error": ""})
    transaction.on_commit(lambda: try_sync_workspace(workspace_id), using=using)
