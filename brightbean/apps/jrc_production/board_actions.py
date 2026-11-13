"""One mutation path for drag, context menu and batch actions."""

import json
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from apps.composer.models import Post
from apps.members.permissions import can_decide_at_step

from .models import PipelineStage
from .services import available_moves, can_create_content, can_decide, content_membership, move_content, pipeline_stage
from .trash import manage_trash, trash_content


def can_request_approval(post, membership):
    return (
        can_create_content(membership)
        and (post.author_id == membership.user_id or membership.effective_permissions.get("edit_others_posts", False))
        and (
            PipelineStage.REVIEW in available_moves(post, membership)
            or bool(post.platform_posts.all())
            and {copy.status for copy in post.platform_posts.all()} <= {"pending_review", "pending_client", "approved"}
            and pipeline_stage(post) == PipelineStage.REVIEW
        )
    )


@transaction.atomic
def board_move(post, stage, user, *, expected_stage, scheduled_at=None, comment="", approval_only=False):
    # Refresh and lock before checking the decision step; another approver may have advanced it.
    post = Post.objects.select_for_update(of=("self",)).select_related("workspace").get(pk=post.pk)
    membership = content_membership(post.workspace, user)
    if pipeline_stage(post) != expected_stage:
        raise ValidationError("This content changed. Refresh the board and try again.")
    if stage == PipelineStage.APPROVED and not can_decide(post, membership):
        if not can_request_approval(post, membership):
            raise PermissionDenied("You cannot approve or request approval for this content at this stage.")
        if PipelineStage.REVIEW in available_moves(post, membership):
            if approval_only and not can_decide_at_step(membership, "pending_review"):
                raise PermissionDenied("You cannot approve this content at this stage.")
            post = move_content(post, PipelineStage.REVIEW, user, expected_stage=expected_stage)
        if not can_decide(post, membership):
            if approval_only:
                raise PermissionDenied("You cannot approve this content at this stage.")
            return post, "Approval requested. It is waiting for the person who approves this step."
        expected_stage = pipeline_stage(post)
    moved = move_content(post, stage, user, expected_stage=expected_stage, scheduled_at=scheduled_at, comment=comment)
    if stage == PipelineStage.APPROVED and pipeline_stage(moved) == PipelineStage.REVIEW:
        return moved, "Approved by the team. It now waits for the client's approval."
    return moved, f"Content moved to {PipelineStage(pipeline_stage(moved)).label}."


@login_required
@require_POST
def batch(request):
    try:
        data = json.loads(request.body)
        action = data["action"]
        items = data["items"]
        if (
            action not in {"move", "approve", "trash", "restore", "purge"}
            or not isinstance(items, list)
            or not 1 <= len(items) <= 100
        ):
            raise ValueError
        ids = [UUID(item["id"]) for item in items]
        if len(set(ids)) != len(ids):
            raise ValueError
        stage = data.get("stage", "")
        if action == "move" and stage not in PipelineStage.values:
            raise ValueError
        comment = data.get("comment", "")
        if not isinstance(comment, str) or len(comment) > 5000:
            raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError):
        return JsonResponse({"error": "Choose 1–100 distinct items and a valid action."}, status=400)
    results = []
    for item, post_id in zip(items, ids, strict=True):
        result = {"id": str(post_id), "ok": False}
        try:
            # Each item has its own transaction: a refusal cannot roll back a successful sibling.
            with transaction.atomic():
                if action in {"restore", "purge"}:
                    manage_trash(post_id, request.user, permanently=action == "purge")
                    message = (
                        "Permanently deleted." if action == "purge" else "Restored without a publication schedule."
                    )
                else:
                    post = Post.objects.select_for_update(of=("self",)).select_related("workspace").get(pk=post_id)
                    content_membership(post.workspace, request.user)
                    if pipeline_stage(post) != item.get("expected_stage"):
                        raise ValidationError("This content changed. Refresh the board and try again.")
                    if action == "trash":
                        trash_content(post, request.user)
                        message = "Moved to Trash."
                    else:
                        from .views import _publish_at

                        scheduled_at = _publish_at(post.workspace, data.get("scheduled_at", ""))
                        post, message = board_move(
                            post,
                            PipelineStage.APPROVED if action == "approve" else stage,
                            request.user,
                            expected_stage=item.get("expected_stage"),
                            scheduled_at=scheduled_at,
                            comment=comment,
                            approval_only=action == "approve",
                        )
                result.update(ok=True, message=message)
        except Post.DoesNotExist:
            result["message"] = "Content not found or no longer available."
        except (PermissionDenied, ValidationError, ValueError) as exc:
            # Never disclose titles or bodies for ids outside the user's workspace.
            result["message"] = " ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
        results.append(result)
    return JsonResponse({"results": results})
