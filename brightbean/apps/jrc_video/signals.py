from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver

from apps.members.models import WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

from .client import FreeFrameClient
from .models import VideoAccount, VideoAccountRevocation, VideoProject
from .services import queue_account, queue_workspace, try_revoke_account


@receiver(pre_save, sender=WorkspaceMembership)
def membership_moving(sender, instance, using, raw=False, **kwargs):
    if raw or not getattr(settings, "JRC_FF_SYNC_ENABLED", False):
        return
    instance._jrc_previous_workspace_id = (
        WorkspaceMembership.objects.using(using).filter(pk=instance.pk).values_list("workspace_id", flat=True).first()
    )


@receiver(post_save, sender=WorkspaceMembership)
@receiver(post_delete, sender=WorkspaceMembership)
def membership_changed(sender, instance, using, raw=False, origin=None, **kwargs):
    if raw or not getattr(settings, "JRC_FF_SYNC_ENABLED", False):
        return
    origin_model = getattr(origin, "model", type(origin))
    if origin_model in {Workspace, Organization}:
        # pre_delete on Workspace already revokes access. Creating a retry row
        # during its cascade would violate its foreign key at transaction end.
        return
    # During cascades the workspace itself may already be gone.
    if Workspace.objects.using(using).filter(pk=instance.workspace_id).exists():
        queue_workspace(instance.workspace_id, using=using)
    previous = getattr(instance, "_jrc_previous_workspace_id", None)
    if previous and previous != instance.workspace_id and Workspace.objects.using(using).filter(pk=previous).exists():
        queue_workspace(previous, using=using)


@receiver(post_save, sender=Workspace)
def workspace_changed(sender, instance, using, raw=False, **kwargs):
    if (
        not raw
        and getattr(settings, "JRC_FF_SYNC_ENABLED", False)
        and VideoProject.objects.using(using).filter(workspace_id=instance.pk).exists()
    ):
        queue_workspace(instance.pk, using=using)


@receiver(post_save, sender=get_user_model())
def user_changed(sender, instance, using, raw=False, update_fields=None, **kwargs):
    if raw or not getattr(settings, "JRC_FF_SYNC_ENABLED", False):
        return
    if update_fields and not {"name", "email", "is_active"}.intersection(update_fields):
        return
    for workspace_id in instance.workspace_memberships.values_list("workspace_id", flat=True):
        queue_workspace(workspace_id, using=using)
    # Accounts without memberships must still be disabled when blocked.
    if VideoAccount.objects.using(using).filter(user=instance).exists() and not instance.workspace_memberships.exists():
        # Durable retry marker first; the API call runs after the commit and a failure
        # must not turn into an error page.
        queue_account(instance.pk, using=using)


@receiver(pre_delete, sender=get_user_model())
def user_deleted(sender, instance, using, **kwargs):
    if not getattr(settings, "JRC_FF_SYNC_ENABLED", False):
        return
    # The collector invokes pre_delete inside a transaction. Wait for any
    # in-flight sync before revoking, so it cannot reactivate a deleted user.
    sender.objects.using(using).select_for_update(no_key=True).get(pk=instance.pk)
    # The marker commits atomically with deletion and survives the user's cascade.
    # A rollback must neither leave a marker nor deactivate a still-existing user.
    user_id = instance.pk
    VideoAccountRevocation.objects.using(using).get_or_create(source_user_id=user_id)
    transaction.on_commit(lambda: try_revoke_account(user_id), using=using)


@receiver(pre_delete, sender=Workspace)
def workspace_deleted(sender, instance, using, **kwargs):
    if not getattr(settings, "JRC_FF_SYNC_ENABLED", False):
        return
    sender.objects.using(using).select_for_update(no_key=True).get(pk=instance.pk)
    if not VideoProject.objects.using(using).filter(workspace_id=instance.pk).exists():
        return
    # Retain all remote media but revoke access before losing the local mapping.
    api = FreeFrameClient()
    remote = api.request("PUT", f"/jrc-sync/projects/{instance.pk}", {"name": instance.name})
    for member in api.request("GET", f"/projects/{remote['id']}/members"):
        if member["user_id"] != remote["service_user_id"]:
            api.request("DELETE", f"/projects/{remote['id']}/members/{member['user_id']}")
