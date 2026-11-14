"""Search only data the actor can read; FreeFrame enforces its own comment visibility."""

from django.db.models import Q

from apps.approvals.comments import get_visible_comments
from apps.jrc_texts.content import content_to_plain_text
from apps.jrc_texts.models import TextVersion
from apps.jrc_texts.services import can_edit_text
from apps.jrc_video.client import FreeFrameError, FreeFrameUserClient
from apps.jrc_video.models import VideoAsset
from apps.members.models import WorkspaceMembership


def search_content(posts, user, query):
    needle = query.casefold()
    matches = set()
    by_workspace = {}
    for post in posts:
        by_workspace.setdefault(post.workspace_id, []).append(post)
        if needle in post.title.casefold() or needle in post.caption.casefold():
            matches.add(post.pk)
        if any(needle in (copy.platform_specific_caption or "").casefold() for copy in post.platform_posts.all()):
            matches.add(post.pk)
    ids = [post.pk for post in posts]
    memberships = {
        membership.workspace_id: membership
        for membership in WorkspaceMembership.objects.filter(user=user, workspace_id__in=by_workspace)
    }
    editable_ids = [post.pk for post in posts if can_edit_text(post, memberships[post.workspace_id])]
    # get_version also requires current editing rights to read one's own private draft.
    versions = TextVersion.objects.filter(document__post_id__in=ids).filter(
        Q(state="final") | Q(state="draft", draft_owner=user, document__post_id__in=editable_ids)
    )
    for version in versions.values("document__post_id", "document__format", "content").iterator(chunk_size=100):
        text = (
            version["content"].get("text", "")
            if version["document__format"] == "plain"
            else content_to_plain_text(version["content"])
        )
        if needle in text.casefold():
            matches.add(version["document__post_id"])
    from .models import ShotlistItem

    matches.update(
        ShotlistItem.objects.filter(post_id__in=ids)
        .filter(Q(text__icontains=query) | Q(note__icontains=query))
        .values_list("post_id", flat=True)
    )
    for group in by_workspace.values():
        pending = list(get_visible_comments(group, user, group[0].workspace))
        while pending:
            comment = pending.pop()
            if needle in comment.body.casefold():
                matches.add(comment.post_id)
            pending.extend(comment.visible_replies)
    warning = ""
    videos = VideoAsset.objects.filter(platform_post__post_id__in=ids).values_list(
        "ff_asset_id", "platform_post__post_id"
    )
    api = FreeFrameUserClient(user)
    for asset_id, post_id in videos:
        try:
            pending = list(api.request("GET", f"/assets/{asset_id}/comments"))
            while pending:
                comment = pending.pop()
                if needle in comment.get("body", "").casefold():
                    matches.add(post_id)
                pending.extend(comment.get("replies", []))
        except FreeFrameError as exc:
            warning = "Video comments could not all be searched. Try again when FreeFrame is available."
            # An inaccessible or missing asset must not hide matches on the remaining videos.
            if exc.status_code not in {403, 404}:
                break
    return [post for post in posts if post.pk in matches], warning
