# JR Content: přidáno 2026-11-07 (blok P1-B36), upraveno 2026-11-07 (blok P1-B37), AGPL-3.0.
"""Editing content after it was approved (upstream "Option A", PM decision P-025 A and its extension).

The single place every edit path goes through: the composer (save in every action, autosave, media
add / upload / remove, Unsplash), the JR detail and REST/MCP caption versions
(``jrc_texts.save_caption``), REST ``PATCH /posts/{id}``, marking a channel's final video
(``jrc_video.player.set_final``) and uploading a channel thumbnail
(``jrc_thumbnails.services.upload_thumbnail``). A writer takes ``reviewed_content(post)`` before it
changes anything and calls ``return_edited_to_review(post, before)`` after; this module decides what
changed and whether the approval mode sends the edited copies back to review
(``edit_returns_to_review``).

"Reviewed content" is what goes out and what a reviewer approves: per channel copy its effective
title, caption and first comment (its own text, or the base text it falls back to), its final video
version and its current (newest) thumbnail, and the tags and media (order and alt text) all copies
share. So a copy goes back to review exactly when what it would publish changed; a base caption edit
leaves a copy with its own caption alone. Production texts that never go out (script, concept, shot
list), team-only notes and the provider settings of automatic channels (``platform_extra``, see
P1-B37 section 4) are not reviewed content.

The return happens from every state that carries the approval and has not gone out yet
(``PlatformPost.REVIEW_RETURN_STATUSES``): approved, waiting for the client, planned, on hold, failed.
A planned copy is taken out of the plan on the way: its time and queue slot are dropped (the time is
kept on the content as its proposed date when nothing else of it stays planned).
"""

from dataclasses import dataclass

from django.apps import apps as django_apps
from django.db.models import Max
from django.utils import timezone

from apps.members.permissions import edit_returns_to_review, is_planned

# Per copy: own field and the base field it falls back to (``PlatformPost.effective_*``).
COPY_FIELDS = (
    ("platform_specific_title", "title"),
    ("platform_specific_caption", "caption"),
    ("platform_specific_first_comment", "first_comment"),
)


@dataclass(frozen=True)
class ReviewedContent:
    shared: tuple
    copies: dict


def _final_videos(post):
    """``{copy id: final version id}`` of the channel videos (one video per copy, ADR-002)."""
    if not django_apps.is_installed("apps.jrc_video"):
        return {}
    from apps.jrc_video.models import VideoAsset

    rows = VideoAsset.objects.filter(platform_post__post_id=post.pk).values_list("platform_post_id", "final_version_id")
    return dict(rows)


def _current_thumbnails(post):
    """``{copy id: number of the newest thumbnail}``; the newest version is the channel's thumbnail (P1-B10)."""
    if not django_apps.is_installed("apps.jrc_thumbnails"):
        return {}
    from apps.jrc_thumbnails.models import ChannelThumbnail

    rows = ChannelThumbnail.objects.filter(post_id=post.pk).values("platform_post_id").annotate(number=Max("number"))
    return {row["platform_post_id"]: row["number"] for row in rows}


def reviewed_content(post):
    """Snapshot of the reviewed content of ``post`` (base fields from the instance, the rest from the database)."""
    from apps.composer.models import PlatformPost, PostMedia

    own_fields = [own for own, _ in COPY_FIELDS]
    videos = _final_videos(post)
    thumbnails = _current_thumbnails(post)
    copies = {}
    for pk, *own in PlatformPost.objects.filter(post_id=post.pk).values_list("pk", *own_fields):
        texts = tuple(
            value if value is not None else getattr(post, base)
            for value, (_, base) in zip(own, COPY_FIELDS, strict=True)
        )
        copies[pk] = (*texts, videos.get(pk), thumbnails.get(pk))
    media = tuple(
        PostMedia.objects.filter(post_id=post.pk).order_by("position", "id").values_list("media_asset_id", "alt_text")
    )
    return ReviewedContent(shared=(tuple(post.tags or ()), media), copies=copies)


def return_edited_to_review(post, before):
    """Send copies whose reviewed content changed since ``before`` back to review (P-025 A).

    Only where the approval mode requires approval (``edit_returns_to_review``): there an edited
    text, video or thumbnail would otherwise go out without anybody approving it. Copies in a state
    that carries the approval (``PlatformPost.REVIEW_RETURN_STATUSES``) move to ``pending_review``.
    A copy that was in the plan (``is_planned``) is taken out of it: its time and queue slot are
    dropped, as unscheduling does; a failed copy loses its past attempt's time the same way. Returns
    the moved copies; each carries ``returned_from`` (its previous status) and ``taken_out_of_plan``
    for ``review_return_notice``. Nothing happens when ``before`` is ``None`` (new content) or
    nothing changed.
    """
    from apps.calendar.models import QueueEntry
    from apps.composer.models import PlatformPost
    from apps.composer.services import sync_post_scheduled_at

    if before is None or not edit_returns_to_review(post.workspace):
        return []
    after = reviewed_content(post)
    whole = before.shared != after.shared
    returned, planned_times, times_dropped = [], [], False
    candidates = PlatformPost.objects.filter(
        post_id=post.pk, status__in=PlatformPost.REVIEW_RETURN_STATUSES
    ).select_related("social_account")
    for pp in candidates:
        if pp.pk not in before.copies:
            continue  # a copy this save created carries no approval
        if not (whole or before.copies[pp.pk] != after.copies.get(pp.pk)):
            continue
        pp.returned_from = pp.status
        pp.taken_out_of_plan = is_planned(pp)
        update_fields = ["status", "updated_at"]
        if pp.status == PlatformPost.Status.SCHEDULED or pp.scheduled_at is not None:
            if pp.taken_out_of_plan and pp.scheduled_at is not None:
                planned_times.append(pp.scheduled_at)
            pp.scheduled_at = None
            update_fields.append("scheduled_at")
            QueueEntry.objects.filter(post_id=post.pk, queue__social_account_id=pp.social_account_id).delete()
            times_dropped = True
        pp.status = PlatformPost.Status.PENDING_REVIEW
        pp.save(update_fields=update_fields)
        returned.append(pp)
    if times_dropped:
        sync_post_scheduled_at(post)
        _keep_planned_time_as_proposal(post, planned_times)
    return returned


def _keep_planned_time_as_proposal(post, planned_times):
    """The dropped planned date stays visible as the content's proposed date (JR detail, board, composer).

    Only when nothing else of the content is still planned (``Post.scheduled_at`` is then empty) and
    there is no proposal yet; a past time is no proposal.
    """
    upcoming = [when for when in planned_times if when > timezone.now()]
    if not upcoming or post.scheduled_at is not None or post.proposed_publish_at is not None:
        return
    post.proposed_publish_at = min(upcoming)
    post.save(update_fields=["proposed_publish_at", "updated_at"])


def _label(pp):
    account = pp.social_account
    return f"{account.get_platform_display()} · {account.account_name}"


def _names(copies):
    labels = [_label(pp) for pp in copies]
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]


def review_return_notice(returned, what="content"):
    """What to tell the person whose edit sent copies back to review ("" when none were).

    ``what`` names the change ("caption", "content", "final video", "thumbnail"). Copies taken out of
    the plan are named as such, so nobody wonders why planned content no longer goes out.
    """
    if not returned:
        return ""
    planned = [pp for pp in returned if getattr(pp, "taken_out_of_plan", False)]
    others = [pp for pp in returned if not getattr(pp, "taken_out_of_plan", False)]
    parts = []
    if planned:
        one = len(planned) == 1
        parts.append(
            f"{_names(planned)} {'was' if one else 'were'} taken out of the plan because the {what} changed. "
            f"{'It goes' if one else 'They go'} back to review for approval; plan {'it' if one else 'them'} "
            f"again once {'it is' if one else 'they are'} approved."
        )
    if others:
        verb = "goes" if len(others) == 1 else "go"
        parts.append(f"{_names(others)} {verb} back to review for approval because the {what} changed.")
    return " ".join(parts)


_HINT_STATE = {
    "approved": "Approved",
    "pending_client": "Waiting for the client",
    "scheduled": "Planned",
    "on_hold": "On hold",
    "failed": "Publishing failed",
}


def review_return_hint(platform_post, change="a changed caption"):
    """The hint next to Save: would saving ``change`` send this copy back to review (and out of the plan)?

    "" where it would not: the mode does not require approval, or the copy carries no approval yet.
    """
    from apps.composer.models import PlatformPost

    if platform_post.status not in PlatformPost.REVIEW_RETURN_STATUSES:
        return ""
    if not edit_returns_to_review(platform_post.post.workspace):
        return ""
    effect = (
        "takes it out of the plan and sends it back for re-approval"
        if is_planned(platform_post)
        else "sends it back for re-approval"
    )
    return f"{_HINT_STATE[platform_post.status]} · saving {change} {effect}"
