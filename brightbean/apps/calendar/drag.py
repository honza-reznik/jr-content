"""Who may move a channel copy on the calendar (P1-B34).

One rule for the chip offered as draggable and for the drop endpoint (``reschedule_post``), so the
calendar never offers a drag the server refuses.
"""

from apps.members.permissions import can_schedule

# Statuses a calendar drop promotes to ``scheduled`` (implicit schedule / retry) rather than merely
# re-timing; that promotion also has to pass the approval workflow.
IMPLICIT_SCHEDULE_STATUSES = ("draft", "failed")


def drop_refusal(pp, membership):
    """``(http_status, message)`` why ``membership`` may not move ``pp`` to another time, or ``None``.

    - only statuses that may move at all (``PlatformPost.RESCHEDULABLE_STATUSES``);
    - the member edits the content (own post or ``edit_others_posts``);
    - every drop changes the copy's planned publication time, whatever its status (draft or failed
      becomes scheduled, scheduled/approved/in review keep their status and move in time), so it
      needs the one planning right ``can_schedule`` (P1-B32), as Set date in the JR detail, the
      composer and REST/MCP do;
    - a drop that schedules a draft must also be allowed by the approval workflow
      (``require_release_allowed`` → ``release_blocker``).

    Whether the copy's content can publish at all (media, Pinterest board) is checked by the endpoint
    only; it needs the post's media and is explained in the refusal message.
    """
    if not pp.is_reschedulable:
        return 400, "Post cannot be rescheduled in its current status."
    permissions = membership.effective_permissions if membership else {}
    if pp.post.author_id != getattr(membership, "user_id", None) and not permissions.get("edit_others_posts", False):
        return 403, "Permission denied."
    if not can_schedule(membership):
        return 403, "You do not have permission to schedule this post."
    if pp.status in IMPLICIT_SCHEDULE_STATUSES and pp.can_transition_to("scheduled"):
        from apps.composer.services import require_release_allowed

        try:
            require_release_allowed(membership.workspace, pp.status)
        except ValueError as exc:
            return 400, str(exc)
    return None
