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
