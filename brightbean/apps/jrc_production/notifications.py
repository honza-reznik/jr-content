"""Notifications about new texts and videos of a content (F7-B03).

Audience: the team (everyone except client and viewer) without the person who made the version.
The client side (client, viewer) only once the content is in Review or later, i.e. at the point
where it is shown to them. The text never quotes the script, caption or any note: only who, what
and which content, so nothing internal can leak through a notification.

A failure here must never undo the save or the video status copy, so every call is isolated.
"""

import logging

from django.db import transaction

from apps.notifications.audience import content_label, content_path, external_members, team_members
from apps.notifications.engine import notify
from apps.notifications.models import EventType

from .models import ProductionStage
from .services import pipeline_stage

logger = logging.getLogger(__name__)


def client_sees_content(post):
    """Clients see a content from the Review stage on (Idea to Editing are production only)."""
    return pipeline_stage(post) not in ProductionStage.values


def version_audience(post, exclude_user=None):
    users = team_members(post.workspace, exclude_user)
    if client_sees_content(post):
        users += external_members(post.workspace, exclude_user)
    return users


def _send(post, users, *, event_type, title, body, tab):
    data = {
        "post_id": str(post.id),
        "workspace_id": str(post.workspace_id),
        "action_url": content_path(post, tab),
    }
    for user in users:
        notify(user=user, event_type=event_type, title=title, body=body, data=data)


def notify_text_version(version, actor):
    """A new saved version of a script or a channel caption."""
    try:
        with transaction.atomic():
            document = version.document
            post = document.post
            label = content_label(post)
            who = actor.display_name if actor else "Someone"
            if document.kind == "script":
                event, tab = EventType.CONTENT_SCRIPT_VERSION, "script"
                title = "New script version"
                body = f'{who} saved version {version.number} of the script for "{label}".'
            else:
                account = document.platform_post.social_account
                event, tab = EventType.CONTENT_CAPTION_VERSION, "caption"
                title = "New caption version"
                body = (
                    f"{who} saved version {version.number} of the {account.get_platform_display()} caption "
                    f'({account.account_name}) for "{label}".'
                )
            _send(post, version_audience(post, actor), event_type=event, title=title, body=body, tab=tab)
    except Exception:  # noqa: BLE001 - a notification problem must not block saving
        logger.exception("Could not send the new-version notification for text version %s", version.pk)


def notify_video_version(version):
    """A video version whose conversion has just finished."""
    try:
        with transaction.atomic():
            post = version.video.platform_post.post
            account = version.video.platform_post.social_account
            who = version.uploaded_by.display_name if version.uploaded_by else "Someone"
            body = (
                f"{who} uploaded video version {version.number} for {account.get_platform_display()} "
                f'({account.account_name}); it is ready to watch in "{content_label(post)}".'
            )
            _send(
                post,
                version_audience(post, version.uploaded_by),
                event_type=EventType.CONTENT_VIDEO_VERSION,
                title="New video version",
                body=body,
                tab="video",
            )
    except Exception:  # noqa: BLE001 - a notification problem must not block the status copy
        logger.exception("Could not send the new-video notification for version %s", version.pk)
