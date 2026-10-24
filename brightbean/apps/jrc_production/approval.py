"""Approval card of the content detail (F7-B02): what is being approved and who decides.

Read-only helpers. Every decision goes through ``services.move_content`` (approve) and
``services.request_content_changes``, which authorize on the server.
"""

from apps.approvals.models import ApprovalAction
from apps.jrc_texts.models import TextDocument, TextVersion
from apps.jrc_texts.services import latest_saved_captions

from .services import can_decide, is_client

_STATUS_TEXT = {
    "draft": "This content is not in review yet.",
    "pending_review": "The team is reviewing this content.",
    "pending_client": "Waiting for the client's approval.",
    "changes_requested": "Changes were requested. The team is working on them and will send a new version.",
    "rejected": "This content was rejected. The team will rework it.",
    "approved": "Approved.",
    "on_hold": "On hold at the client's request.",
    "scheduled": "Approved and planned for publication.",
    "publishing": "Approved and being published.",
    "failed": "Approved. Publishing failed; the team is looking into it.",
    "published": "Published.",
    "partially_published": "Published on some channels.",
}

# Order in which a mixed set of channel statuses is described (the earliest step wins).
_STATUS_ORDER = list(_STATUS_TEXT)


def post_statuses(post):
    return {pp.status for pp in post.platform_posts.all()}


def is_waiting_for(post, membership):
    """Content that only this client can decide on now (the "Waiting for you" list)."""
    return is_client(membership) and can_decide(post, membership)


def status_text(post, membership):
    statuses = post_statuses(post)
    if can_decide(post, membership, statuses):
        return "Waiting for your decision."
    if is_client(membership) and "pending_review" in statuses:
        mode = post.workspace.approval_workflow_mode
        if mode == "required_internal_and_client":
            return "The team is checking this first. You will be asked to approve after that."
    for status in _STATUS_ORDER:
        if status in statuses:
            return _STATUS_TEXT[status]
    return ""


def _script_summary(post):
    document = TextDocument.objects.filter(post=post, kind=TextDocument.Kind.SCRIPT).first()
    version = document.versions.filter(state=TextVersion.State.FINAL).order_by("-number").first() if document else None
    if version is None:
        return None
    return {
        "number": version.number,
        "text": version.text,
        "saved_at": version.finalized_at or version.created_at,
    }


def _caption_summaries(post, accounts):
    variants = {v.social_account_id: v for v in post.platform_posts.all()}
    saved = latest_saved_captions([v.pk for v in variants.values()])
    rows = []
    for account in accounts:
        variant = variants.get(account.pk)
        if variant is None:
            continue
        version = saved.get(variant.pk)
        text = version.text if version else variant.effective_caption
        rows.append(
            {
                "label": f"{account.get_platform_display()} · {account.account_name}",
                "text": text,
                "number": version.number if version else None,
            }
        )
    return rows


def _video_summaries(post, accounts):
    from apps.jrc_video.models import VideoAsset, VideoVersion

    videos = {
        video.platform_post_id: video
        for video in VideoAsset.objects.filter(platform_post__post=post)
        .select_related("final_version", "final_by")
        .prefetch_related("versions")
    }
    variants = {v.social_account_id: v for v in post.platform_posts.all()}
    rows = []
    for account in accounts:
        variant = variants.get(account.pk)
        video = videos.get(variant.pk) if variant else None
        label = f"{account.get_platform_display()} · {account.account_name}"
        if video is None:
            rows.append({"label": label, "kind": "none"})
            continue
        if video.final_version_id:
            version = video.final_version
            rows.append(
                {
                    "label": label,
                    "kind": "final",
                    "number": version.number,
                    "by": (video.final_by.display_name or video.final_by.email) if video.final_by else "",
                    "at": video.final_at,
                    "duration": _duration(version),
                }
            )
            continue
        ready = [v for v in video.versions.all() if v.status == VideoVersion.Status.READY]
        latest = max(ready, key=lambda v: v.number, default=None)
        if latest is None:
            rows.append({"label": label, "kind": "none"})
        else:
            rows.append({"label": label, "kind": "latest", "number": latest.number, "duration": _duration(latest)})
    return rows


def _duration(version):
    if not version.duration_s:
        return ""
    return f"{int(version.duration_s // 60)}:{int(version.duration_s % 60):02d}"


def _latest_decision(post, can_view_internal_notes):
    action = (
        ApprovalAction.objects.filter(post=post)
        .exclude(action__in=[ApprovalAction.ActionType.SUBMITTED, ApprovalAction.ActionType.RESUBMITTED])
        .select_related("user")
        .order_by("-created_at")
        .first()
    )
    if action is None:
        return None
    return {
        "label": action.get_action_display(),
        "kind": action.action,
        "actor": (action.user.display_name or action.user.email) if action.user else "System",
        "at": action.created_at,
        "note": action.comment if (can_view_internal_notes or action.note_visibility == "external") else "",
    }


def approval_context(post, membership, accounts, *, can_view_internal_notes):
    """Everything the Approval tab shows: summary of what is approved and the allowed decisions."""
    statuses = post_statuses(post)
    decide = can_decide(post, membership, statuses)
    return {
        "approval_script": _script_summary(post),
        "approval_captions": _caption_summaries(post, accounts),
        "approval_videos": _video_summaries(post, accounts),
        "approval_status": status_text(post, membership),
        "approval_can_decide": decide,
        "approval_latest": _latest_decision(post, can_view_internal_notes),
        "approval_two_stage": post.workspace.approval_workflow_mode == "required_internal_and_client",
    }
