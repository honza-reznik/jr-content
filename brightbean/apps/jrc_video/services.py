import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.members.models import WorkspaceMembership
from apps.workspaces.models import Workspace

from .client import FreeFrameClient, FreeFrameError
from .models import VideoAccount, VideoAccountSyncRequest, VideoProject, VideoSyncRequest

logger = logging.getLogger(__name__)
ROLE_MAP = {
    "owner": "editor",
    "manager": "editor",
    "editor": "editor",
    "contributor": "editor",
    "client": "reviewer",
    "viewer": "viewer",
}


def error_text(exc):
    # VideoSyncRequest.last_error / VideoAccountSyncRequest.last_error are 255 chars.
    return str(exc)[:255]


def sync_account(user_id, api):
    """PUT one account; the mapping is stored in its own short transaction right away."""
    # The row lock serializes requests for a user who belongs to several workspaces
    # and with user deletion (which must not be undone by an in-flight sync).
    with transaction.atomic():
        user = get_user_model().objects.select_for_update(no_key=True).get(pk=user_id)
        requested_at = VideoAccountSyncRequest.objects.filter(user=user).values_list("requested_at", flat=True).first()
        remote = api.request(
            "PUT",
            f"/jrc-sync/accounts/{user.pk}",
            {"email": user.email, "name": user.display_name, "is_active": user.is_active},
        )
        account, _ = VideoAccount.objects.update_or_create(
            user=user, defaults={"ff_user_id": remote["id"], "synced_at": timezone.now()}
        )
        VideoAccountSyncRequest.objects.filter(user=user, requested_at=requested_at).delete()
        return account


def _apply(api, errors, method, path, payload=None):
    try:
        api.request(method, path, payload)
    except FreeFrameError as exc:
        errors.append(str(exc))


def sync_workspace(workspace_id, *, api=None):
    """Synchronize one workspace. A failure of one account or member never stops the others.

    Revocations run first. Collected errors keep VideoSyncRequest for a retry and are
    raised as one FreeFrameError at the end (after the successful parts are committed).
    """
    api = api or FreeFrameClient()
    errors = []
    failed_users = set()
    user_ids = (
        WorkspaceMembership.objects.filter(workspace_id=workspace_id)
        .order_by("user_id")
        .values_list("user_id", flat=True)
        .distinct()
    )
    for user_id in user_ids:
        try:
            sync_account(user_id, api)
        except get_user_model().DoesNotExist:
            # A concurrent user deletion already revoked the remote account.
            continue
        except FreeFrameError as exc:
            # Keep going: other members' revocations must not depend on this account.
            failed_users.add(user_id)
            errors.append(str(exc))
    with transaction.atomic():
        workspace = Workspace.objects.select_for_update(no_key=True).get(pk=workspace_id)
        requested_at = (
            VideoSyncRequest.objects.filter(workspace=workspace).values_list("requested_at", flat=True).first()
        )
        remote = api.request("PUT", f"/jrc-sync/projects/{workspace.pk}", {"name": workspace.name})
        project_id = remote["id"]
        service_id = remote["service_user_id"]
        members = api.request("GET", f"/projects/{project_id}/members")
        existing = {member["user_id"]: member["role"] for member in members}
        memberships = list(
            WorkspaceMembership.objects.filter(workspace=workspace).select_related("user").order_by("user_id")
        )
        accounts = {
            account.user_id: account
            for account in VideoAccount.objects.filter(user_id__in=[m.user_id for m in memberships])
        }
        desired = {}
        for membership in memberships:
            account = accounts.get(membership.user_id)
            if account is None:
                # Never synchronized (failed now or added concurrently): no access yet, retry later.
                if membership.user_id not in failed_users:
                    errors.append(f"Account {membership.user_id} is not synchronized yet.")
                continue
            # A failed account PUT with an existing mapping keeps its remote identity, so its
            # membership is still revoked or adjusted below; it is never granted more.
            if membership.user.is_active and not workspace.is_archived:
                # Unknown/custom roles must not accidentally grant upload permission.
                desired[str(account.ff_user_id)] = (
                    "viewer" if membership.custom_role_id else ROLE_MAP.get(membership.workspace_role, "viewer")
                )
        if desired.pop(service_id, None):
            errors.append("The service account cannot be a workspace member.")
        # Revocations first, each independent of the others.
        for user_id in sorted(existing.keys() - desired.keys() - {service_id}):
            _apply(api, errors, "DELETE", f"/projects/{project_id}/members/{user_id}")
        for user_id, role in sorted(desired.items()):
            if user_id in existing and existing[user_id] != role:
                _apply(api, errors, "PATCH", f"/projects/{project_id}/members/{user_id}", {"role": role})
        for user_id, role in sorted(desired.items()):
            if user_id not in existing:
                _apply(api, errors, "POST", f"/projects/{project_id}/members", {"user_id": user_id, "role": role})
        project, _ = VideoProject.objects.update_or_create(
            workspace=workspace, defaults={"ff_project_id": project_id, "synced_at": timezone.now()}
        )
        if errors:
            summary = f"{len(errors)} FreeFrame error(s); first: {errors[0]}"
            VideoSyncRequest.objects.update_or_create(workspace=workspace, defaults={"last_error": error_text(summary)})
        else:
            # Preserve a newer change queued while the remote API was in flight.
            VideoSyncRequest.objects.filter(workspace=workspace, requested_at=requested_at).delete()
    if errors:
        for error in errors:
            logger.error("FreeFrame synchronization error for workspace %s: %s", workspace_id, error)
        raise FreeFrameError(summary)
    return project


def try_sync_workspace(workspace_id):
    try:
        sync_workspace(workspace_id)
    except Workspace.DoesNotExist:
        return
    except FreeFrameError as exc:
        VideoSyncRequest.objects.filter(workspace_id=workspace_id).update(last_error=error_text(exc))
        logger.error("FreeFrame synchronization failed for workspace %s: %s", workspace_id, exc)


def mark_account_failed(user_id, exc):
    if get_user_model().objects.filter(pk=user_id).exists():
        VideoAccountSyncRequest.objects.update_or_create(user_id=user_id, defaults={"last_error": error_text(exc)})


def try_sync_account(user_id):
    """Account without memberships (e.g. blocked); `jrc_sync_freeframe --pending` retries it."""
    try:
        sync_account(user_id, FreeFrameClient())
    except get_user_model().DoesNotExist:
        return
    except FreeFrameError as exc:
        mark_account_failed(user_id, exc)
        logger.error("FreeFrame account synchronization failed for user %s: %s; run jrc_sync_freeframe.", user_id, exc)


def queue_workspace(workspace_id, *, using="default"):
    VideoSyncRequest.objects.using(using).update_or_create(workspace_id=workspace_id, defaults={"last_error": ""})
    transaction.on_commit(lambda: try_sync_workspace(workspace_id), using=using)


def queue_account(user_id, *, using="default"):
    VideoAccountSyncRequest.objects.using(using).update_or_create(user_id=user_id, defaults={"last_error": ""})
    transaction.on_commit(lambda: try_sync_account(user_id), using=using)
