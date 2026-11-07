# JR Content: přidáno 2026-11-07 (blok P1-B36), AGPL-3.0.
"""Editing content after it was approved (upstream "Option A", PM decision P-025 A).

The single place every edit path goes through: the composer (save, autosave, media add / upload /
remove), the JR detail and REST/MCP caption versions (``jrc_texts.save_caption``) and REST
``PATCH /posts/{id}``. A writer takes ``reviewed_content(post)`` before it changes anything and calls
``return_edited_to_review(post, before)`` after; this module decides what changed and whether the
approval mode sends the edited copies back to review (``edit_returns_to_review``).

"Reviewed content" is what goes out and what a reviewer approves: per channel copy its effective
title, caption and first comment (its own text, or the base text it falls back to), and the tags and
media (order and alt text) all copies share. So a copy goes back to review exactly when what it would
publish changed; a base caption edit leaves a copy with its own caption alone. Production texts that
never go out (script, concept, shot list) and team-only notes are not reviewed content.
"""

from dataclasses import dataclass

from apps.members.permissions import edit_returns_to_review

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


def reviewed_content(post):
    """Snapshot of the reviewed content of ``post`` (base fields from the instance, the rest from the database)."""
    from apps.composer.models import PlatformPost, PostMedia

    own_fields = [own for own, _ in COPY_FIELDS]
    copies = {}
    for pk, *own in PlatformPost.objects.filter(post_id=post.pk).values_list("pk", *own_fields):
        copies[pk] = tuple(
            value if value is not None else getattr(post, base)
            for value, (_, base) in zip(own, COPY_FIELDS, strict=True)
        )
    media = tuple(
        PostMedia.objects.filter(post_id=post.pk).order_by("position", "id").values_list("media_asset_id", "alt_text")
    )
    return ReviewedContent(shared=(tuple(post.tags or ()), media), copies=copies)


def return_edited_to_review(post, before):
    """Send approved copies whose reviewed content changed since ``before`` back to review.

    Only where the approval mode requires approval (``edit_returns_to_review``): there an edited text
    would otherwise go out without anybody approving it. Returns the copies moved to
    ``pending_review``; nothing happens when ``before`` is ``None`` (new content) or nothing changed.
    """
    from apps.composer.models import PlatformPost

    if before is None or not edit_returns_to_review(post.workspace):
        return []
    after = reviewed_content(post)
    whole = before.shared != after.shared
    reverted = []
    for pp in PlatformPost.objects.filter(post_id=post.pk, status=PlatformPost.Status.APPROVED):
        if not (whole or before.copies.get(pp.pk) != after.copies.get(pp.pk)):
            continue
        if pp.can_transition_to("pending_review"):
            pp.transition_to("pending_review")
            pp.save(update_fields=[*PlatformPost.TRANSITION_FIELDS, "updated_at"])
            reverted.append(pp)
    return reverted


def edit_reopens_review(platform_post):
    """Would saving a change to this copy send it back to review? For the hints next to Save."""
    return platform_post.status == "approved" and edit_returns_to_review(platform_post.post.workspace)
