"""Shared workspace permission checks."""

from django.core.exceptions import PermissionDenied


def require_authored_content(permissions, user_id, author_id):
    """Editing/deleting requires create rights and ownership or edit-others rights."""
    if not permissions.get("create_posts", False) or (
        author_id != user_id and not permissions.get("edit_others_posts", False)
    ):
        raise PermissionDenied("You cannot change this content.")


def can_schedule(membership):
    """Who may plan content to go out: set its publication date, schedule, queue or publish it now (P1-B32).

    The one rule for every planning path: JR detail (Set date) and board (Ready), composer (Schedule,
    Publish now, Add to queue / Prioritise, chip), calendar (drag, re-timing, bulk Publish), CSV import
    with dates, REST and MCP. It needs ``publish_directly``; a viewer never plans, even with a custom
    role. ``membership`` may also be an API key's membership: the plain key shim has no
    ``workspace_role`` and its permissions already are the key's grants AND the issuer's. Whether the
    copy itself may go out yet is decided separately by the approval workflow (``release_blocker``).
    """
    if not membership or getattr(membership, "workspace_role", None) == "viewer":
        return False
    user = getattr(membership, "user", None)
    workspace = getattr(membership, "workspace", None)
    if user is None or not user.is_active or workspace is None or workspace.is_archived:
        return False
    return bool(membership.effective_permissions.get("publish_directly", False))


def is_planned(platform_post):
    """Is this channel copy in the plan: scheduled to go out, or carrying a planned time (P1-B36)?

    A failed copy is not: its time is the past attempt and it goes nowhere until somebody retries it
    (which is planning again). Published and publishing copies never go back to a draft.
    """
    if platform_post.status in ("failed", "published", "publishing"):
        return False
    return platform_post.status == "scheduled" or platform_post.scheduled_at is not None


def can_unschedule(membership, post):
    """Who may take planned content (``is_planned``) out of the plan: back to a draft, its planned time dropped (P1-B36).

    Taking content out of the plan changes the plan just as planning it does, so it needs the one
    planning right ``can_schedule``; and it changes that content, so also the right to edit it (own
    content with ``create_posts``, anybody else's with ``edit_others_posts``,
    ``require_authored_content``). The one rule for every such path: calendar bulk Draft, the
    composer chip back to Draft, removing a post from a queue, the JR board moving planned content
    back to a production stage, REST ``/cancel`` and MCP ``cancel_post``. ``membership`` may be an API
    key's membership (its ``user`` is the key's issuer).
    """
    if not can_schedule(membership):
        return False
    user = getattr(membership, "user", None)
    try:
        require_authored_content(membership.effective_permissions, getattr(user, "pk", None), post.author_id)
    except PermissionDenied:
        return False
    return True


def content_delete_blocker(membership, post, copies):
    """Why this member may not delete ``copies`` (channel copies of ``post``, or all of them with the content), or "".

    The one rule for every deletion path (P1-B37): the composer's Delete (whole content or one
    channel), the channels deselected when the composer saves, the calendar's bulk Delete, and
    removing a channel on the JR detail and through REST. Deleting changes content, so it needs the
    right to edit it (own content with ``create_posts``, anybody else's with ``edit_others_posts``,
    ``require_authored_content``); deleting a planned copy (``is_planned``) also takes it out of the
    plan, so that needs ``can_unschedule`` as well, exactly as moving it back to Draft does.
    """
    if not membership:
        return "You cannot delete this content."
    user = getattr(membership, "user", None)
    try:
        require_authored_content(membership.effective_permissions, getattr(user, "pk", None), post.author_id)
    except PermissionDenied:
        return "You cannot delete this content."
    if any(is_planned(pp) for pp in copies) and not can_unschedule(membership, post):
        return (
            "This content is planned. Only someone who can schedule content may delete it, because deleting "
            "takes it out of the plan."
        )
    return ""


def can_delete_content(membership, post, copies):
    return not content_delete_blocker(membership, post, copies)


# REST ``/cancel`` and MCP ``cancel_post`` refusal (P1-B37): name both rights ``can_unschedule`` needs.
UNSCHEDULE_REFUSAL = (
    "Permission denied: taking content out of the plan needs publish_directly and the right to edit this post "
    "(create_posts, plus edit_others_posts for another author's post)."
)


def can_publish_directly(membership):
    """Whether this workspace member may mark content as published and download final videos.

    Wider than ``can_schedule``: the client also marks manual publications and downloads the final
    files (JR rule since F5-B07), but never plans them.
    """
    return bool(
        membership
        and membership.workspace_role != "viewer"
        and (membership.workspace_role == "client" or membership.effective_permissions.get("publish_directly", False))
    )


# P1-B27: the approval workflow mode of a workspace (``Workspace.ApprovalWorkflowMode``) is read only here.
# ``can_decide_at_step`` says who decides a review step; ``release_blocker`` says what must happen before
# a channel copy may go out: be scheduled or published (any path: JR detail/board, composer, calendar,
# CSV import, REST, MCP) or have its final files downloaded. The texts explaining a disabled action come
# from the same rule, so the page never promises something the server refuses.
APPROVAL_REQUIRED_MODES = frozenset({"required_internal", "required_internal_and_client"})
TWO_STAGE_MODE = "required_internal_and_client"

_RELEASE_BLOCKED_BY_STATUS = {
    "pending_review": "It is waiting for a review decision.",
    "pending_client": "It is waiting for the client's approval.",
    "changes_requested": "Changes were requested. Move the content back to Editing and finish them first.",
    "rejected": "It was rejected. Move the content back to Editing and rework it first.",
    "on_hold": "It is on hold at the client's request.",
}


def approval_required(workspace):
    """Must every channel copy be approved before it goes out ("Internal review required" and the two-stage mode)?

    "No approvals" and "Optional approvals" let anyone with publishing rights schedule or publish a
    draft directly; in "Optional" a review is offered but not required.
    """
    return workspace.approval_workflow_mode in APPROVAL_REQUIRED_MODES


def review_available(workspace):
    """Is there a review step at all? Not in "No approvals" (PM decision 6. 11., P1-B32).

    The one source for offering and accepting a submission to review: the board and JR detail
    (``available_moves``, also behind REST/MCP stage), the composer buttons and the approval
    services every submission goes through. "Optional approvals" keeps the voluntary review.
    """
    return workspace.approval_workflow_mode != "none"


def edit_returns_to_review(workspace):
    """Does editing approved content send it back to review (upstream "Option A")? (P1-B34)

    Only where the mode requires approval: there an edit would otherwise go out unreviewed. In
    "No approvals" there is no review step at all and in "Optional approvals" a draft may go out
    without one, so an edit keeps the content approved instead of making it wait for a decision
    nobody has to take. The single rule for every place that reverts on an edit (composer save,
    autosave, media add/upload/remove).
    """
    return approval_required(workspace)


def client_approval_required(workspace):
    """Two-stage mode: the team approves first, then the client signs off."""
    return workspace.approval_workflow_mode == TWO_STAGE_MODE


def approval_requirement_text(workspace):
    """What approval this workspace needs before content goes out, for hints; empty when none is needed."""
    if client_approval_required(workspace):
        return "approval by the team and then the client"
    if approval_required(workspace):
        return "approval by the team"
    return ""


def release_blocker(workspace, status):
    """Why a channel copy in ``status`` may not go out yet because of the approval workflow, or "".

    "Go out" means being scheduled, published (also marked as published manually) or having its
    final video and thumbnail downloaded. Copies that already went out (scheduled, publishing,
    published, failed) and approved copies are never blocked by the workflow; a draft only when the
    mode requires approval. Other conditions (publishing rights, caption, final video, channel kind)
    are checked by the caller.
    """
    if status == "draft":
        if approval_required(workspace):
            return f"It needs {approval_requirement_text(workspace)} first."
        return ""
    return _RELEASE_BLOCKED_BY_STATUS.get(status, "")


def can_release(workspace, status):
    return not release_blocker(workspace, status)


def can_decide_at_step(membership, step):
    """Who may decide (approve / request changes / reject) on a copy waiting in ``step`` (P1-B25).

    The single rule behind every decision path: upstream approvals (single, bundled, bulk, chip),
    the JR board/detail, REST and MCP. The workflow side is determined by the built-in role, never
    by custom permission grants: a viewer never decides, a client only at the client step of the
    two-stage workflow (team first, then client); the team decides every other review step. Copies
    left in ``pending_client`` after a switch to a single-stage mode stay decidable by the team.
    """
    if not membership or not membership.user.is_active or membership.workspace.is_archived:
        return False
    if membership.workspace_role == "viewer" or not membership.effective_permissions.get("approve_posts", False):
        return False
    two_stage = client_approval_required(membership.workspace)
    if membership.workspace_role == "client":
        return two_stage and step == "pending_client"
    if two_stage:
        return step == "pending_review"
    return step in {"draft", "pending_review", "pending_client", "rejected", "changes_requested"}


def can_comment(membership):
    """Active clients and content collaborators may comment; viewers only read."""
    return bool(
        membership
        and membership.user.is_active
        and not membership.workspace.is_archived
        and membership.workspace_role != "viewer"
        and (
            membership.workspace_role == "client"
            or membership.effective_permissions.get("create_posts", False)
            or membership.effective_permissions.get("approve_posts", False)
        )
    )


def can_moderate_comments(membership):
    """Only team approvers may resolve threads or delete someone else's comment."""
    return bool(
        can_comment(membership)
        and membership.workspace_role != "client"
        and membership.effective_permissions.get("approve_posts", False)
    )
