"""Who is told about content events, and where the notification links to (F7-B03).

Roles follow the rule used everywhere else in JR Content (``note_audience``, ``protect_note_body``,
``can_decide``): the built-in ``workspace_role`` decides, a custom role does not change the side.
``client`` and ``viewer`` are external; every other member belongs to the team.
"""

from django.urls import reverse

from apps.members.models import WorkspaceMembership

EXTERNAL_ROLES = (
    WorkspaceMembership.WorkspaceRole.CLIENT,
    WorkspaceMembership.WorkspaceRole.VIEWER,
)

CONTENT_TABS = ("script", "video", "caption", "approval", "comments", "history")


def is_external(membership):
    return membership.workspace_role in EXTERNAL_ROLES


def active_memberships(workspace):
    """Members of a live client whose account is still active."""
    if workspace.is_archived:
        return WorkspaceMembership.objects.none()
    return WorkspaceMembership.objects.filter(workspace=workspace, user__is_active=True).select_related(
        "user", "custom_role"
    )


def team_members(workspace, exclude_user=None):
    """Users of the team side (not client, not viewer), without ``exclude_user``."""
    return [
        m.user
        for m in active_memberships(workspace).exclude(workspace_role__in=EXTERNAL_ROLES)
        if exclude_user is None or m.user_id != exclude_user.pk
    ]


def external_members(workspace, exclude_user=None):
    """Users of the client side (client and viewer), without ``exclude_user``."""
    return [
        m.user
        for m in active_memberships(workspace).filter(workspace_role__in=EXTERNAL_ROLES)
        if exclude_user is None or m.user_id != exclude_user.pk
    ]


def content_path(post, tab="approval"):
    """Relative address of the content detail on the given tab (shown only to people who may open it)."""
    if tab not in CONTENT_TABS:
        raise ValueError(f"Unknown content tab: {tab}")
    path = reverse("jrc_production:content_detail", kwargs={"workspace_id": post.workspace_id, "post_id": post.id})
    return f"{path}?tab={tab}"


def content_label(post):
    """A short name of the content for notification texts: the title, never any note or comment."""
    return (post.title or "").strip() or "Untitled content"


def decides_at_step(workspace, membership, step):
    """Is this member the one to decide while the content waits in ``step`` (F7-B03, mirrors ``can_decide``)?

    ``step`` is ``pending_review`` or ``pending_client``. One-stage workspaces: anyone with
    ``approve_posts``. Two-stage (team first, then client): the team decides in ``pending_review``,
    the client in ``pending_client``.
    """
    if not membership.effective_permissions.get("approve_posts", False):
        return False
    if workspace.approval_workflow_mode != "required_internal_and_client":
        return True
    is_client_side = membership.workspace_role == WorkspaceMembership.WorkspaceRole.CLIENT
    return is_client_side if step == "pending_client" else not is_client_side
