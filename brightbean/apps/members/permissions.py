"""Shared workspace permission checks."""

from django.core.exceptions import PermissionDenied


def require_authored_content(permissions, user_id, author_id):
    """Editing/deleting requires create rights and ownership or edit-others rights."""
    if not permissions.get("create_posts", False) or (
        author_id != user_id and not permissions.get("edit_others_posts", False)
    ):
        raise PermissionDenied("You cannot change this content.")


def can_publish_directly(membership):
    """Whether this workspace member may publish content and download final videos."""
    return bool(
        membership
        and membership.workspace_role != "viewer"
        and (membership.workspace_role == "client" or membership.effective_permissions.get("publish_directly", False))
    )


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
    two_stage = membership.workspace.approval_workflow_mode == "required_internal_and_client"
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
