"""Recheck note access before exposing stored notification text."""

from django.core.exceptions import ValidationError

from apps.members.models import WorkspaceMembership

from .models import EventType

GENERIC_NOTE_BODIES = {
    EventType.POST_CHANGES_REQUESTED: "Changes were requested on your post.",
    EventType.POST_REJECTED: "Your post was rejected.",
    EventType.COMMENT_MENTION: "You were mentioned in a comment on a post.",
}


def protect_note_body(notification):
    """Redact inaccessible quotes, including legacy rows with no audience marker.

    The audience records visibility at creation; membership is checked afresh
    for every read/send, so a role change also protects already queued mail.
    Only the in-memory presentation is changed here.
    """
    generic = GENERIC_NOTE_BODIES.get(notification.event_type)
    if generic is None:
        return notification

    try:
        membership = WorkspaceMembership.objects.filter(
            workspace_id=notification.data.get("workspace_id"),
            workspace__is_archived=False,
            user_id=notification.user_id,
            user__is_active=True,
        ).first()
    except (ValidationError, ValueError, TypeError):
        membership = None

    can_read = membership is not None and (
        notification.data.get("note_visibility", "internal") == "external"
        or membership.workspace_role not in ("client", "viewer")
    )
    if not can_read:
        notification.body = generic
    return notification
