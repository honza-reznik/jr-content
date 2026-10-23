from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, render

from apps.approvals.models import ApprovalAction
from apps.composer.models import Post
from apps.jrc_production.services import pipeline_stage
from apps.members.models import WorkspaceMembership
from apps.workspaces.models import Workspace

TABS = ("script", "video", "caption", "approval", "history")


@login_required
def content_detail(request, workspace_id, post_id):
    """Show a workspace member the shared content page and its audit history."""
    workspace = get_object_or_404(Workspace, pk=workspace_id)
    membership = WorkspaceMembership.objects.filter(user=request.user, workspace=workspace).first()
    if membership is None:
        raise PermissionDenied("You are not a member of this workspace.")

    post = get_object_or_404(
        Post.objects.filter(workspace=workspace)
        .select_related("author", "category", "jrc_production__stage_changed_by")
        .prefetch_related("platform_posts__social_account"),
        pk=post_id,
    )
    active_tab = request.GET.get("tab", "script")
    if active_tab not in TABS:
        active_tab = "script"

    can_view_internal_notes = membership.workspace_role not in ("client", "viewer")
    history = [
        {
            "kind": "stage",
            "created_at": change.created_at,
            "label": f"Moved from {change.get_from_stage_display()} to {change.get_to_stage_display()}",
            "actor": change.user,
            "note": change.note,
            "can_view_note": can_view_internal_notes,
        }
        for change in post.jrc_stage_changes.select_related("user").all()
    ]
    approval_actions = list(ApprovalAction.objects.filter(post=post).select_related("user"))
    external_actor_ids = set(
        WorkspaceMembership.objects.filter(
            workspace=workspace,
            user_id__in=[action.user_id for action in approval_actions if action.user_id],
            workspace_role__in=(
                WorkspaceMembership.WorkspaceRole.CLIENT,
                WorkspaceMembership.WorkspaceRole.VIEWER,
            ),
        ).values_list("user_id", flat=True)
    )
    history.extend(
        {
            "kind": "approval",
            "created_at": action.created_at,
            "label": action.get_action_display(),
            "actor": action.user,
            "note": action.comment,
            "can_view_note": can_view_internal_notes or action.user_id in external_actor_ids,
        }
        for action in approval_actions
    )
    history.sort(key=lambda item: item["created_at"], reverse=True)

    return render(
        request,
        "jrc_production/content_detail.html",
        {
            "workspace": workspace,
            "post": post,
            "pipeline_stage": pipeline_stage(post),
            "active_tab": active_tab,
            "tabs": TABS,
            "history": history[:100],
            "can_view_internal_notes": can_view_internal_notes,
            "can_edit_posts": membership.effective_permissions.get("create_posts", False),
        },
    )
