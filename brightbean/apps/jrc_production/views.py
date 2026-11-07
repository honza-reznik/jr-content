import difflib
import re
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.approvals import comments as comment_services
from apps.approvals.edits import edit_reopens_review
from apps.approvals.models import ApprovalAction, PostComment
from apps.composer.models import PlatformPost, Post
from apps.jrc_texts import services as text_services
from apps.jrc_texts.models import TextDocument, TextVersion
from apps.jrc_texts.services import ConflictError
from apps.jrc_video.downloads import can_download_final, missing_for_publication
from apps.members.models import WorkspaceMembership
from apps.members.permissions import can_comment, can_moderate_comments, can_publish_directly, can_schedule
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace

from . import shotlist as shotlist_services
from .approval import approval_context, is_waiting_for
from .forms import PublicationDateForm
from .models import ManualPublication, PipelineStage, ShotlistChange
from .services import (
    add_content_channel,
    available_moves,
    can_create_content,
    can_manage_channels,
    create_content,
    mark_published_manually,
    move_content,
    pipeline_stage,
    publication_blocker,
    remove_content_channel,
    request_content_changes,
    set_publication_date,
)

BOARD_CLIENT_KEY = "jrc_board_client"
IDEA_CLIENT_KEY = "jrc_board_idea_client"
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _memberships(user):
    return list(
        WorkspaceMembership.objects.filter(user=user, workspace__is_archived=False)
        .select_related("workspace", "custom_role")
        .order_by("workspace__name")
    )


def _visible_comment_counts(posts, user):
    """Count comments the viewer may read (clients see external threads only)."""
    by_workspace = {}
    for post in posts:
        by_workspace.setdefault(post.workspace_id, []).append(post)
    counts = {}
    for group in by_workspace.values():
        pending = list(comment_services.get_visible_comments(group, user, group[0].workspace))
        while pending:
            comment = pending.pop()
            counts[comment.post_id] = counts.get(comment.post_id, 0) + 1
            pending.extend(comment.visible_replies)
    return counts


def _board_card(post, membership, comment_count=0):
    stage = pipeline_stage(post)
    variants = post.platform_posts.all()
    planned = post.scheduled_at
    moves = available_moves(post, membership)
    return {
        "post": post,
        "stage": stage,
        "moves": [(value, PipelineStage(value).label) for value in moves],
        # Ready needs a date; offered from Approved, and from a draft where approval is not required (P1-B27).
        "can_plan": PipelineStage.READY in moves,
        "client_review": any(pp.status == "pending_client" for pp in variants),
        "date": planned or post.proposed_publish_at,
        "date_label": "Planned" if planned else "Proposed",
        "comment_count": comment_count,
    }


def _sort_cards(cards, *, newest_first=False):
    """Publication date first (dated cards before undated), then the latest change."""
    cards.sort(key=lambda card: card["post"].updated_at, reverse=True)
    dated = [card for card in cards if card["date"]]
    dated.sort(key=lambda card: card["date"], reverse=newest_first)
    return dated + [card for card in cards if not card["date"]]


def _board_context(request, *, error="", title="", selected_client=None):
    memberships = _memberships(request.user)
    by_workspace = {str(m.workspace_id): m for m in memberships}
    if selected_client is not None:
        selected = str(selected_client)
    elif "client" in request.GET or "channel" in request.GET:
        selected = request.GET.get("client", "")
    else:
        # No explicit filter: reopen the board on the client chosen last in this session.
        selected = request.session.get(BOARD_CLIENT_KEY, "")
        if selected not in by_workspace:
            selected = ""
    channel = request.GET.get("channel", "")
    workspace_ids = list(by_workspace)
    if selected:
        if selected not in by_workspace:
            raise ValidationError("Choose a client you have access to.")
        workspace_ids = [selected]
    channels = list(
        SocialAccount.objects.filter(workspace_id__in=workspace_ids, jrc_channel__isnull=False)
        .select_related("workspace", "jrc_channel")
        .order_by("workspace__name", "account_name")
    )
    if channel and channel != "none" and channel not in {str(c.pk) for c in channels}:
        raise ValidationError("Choose a channel for the selected client.")
    if request.session.get(BOARD_CLIENT_KEY, "") != selected:
        request.session[BOARD_CLIENT_KEY] = selected
    posts = Post.objects.filter(workspace_id__in=workspace_ids)
    if channel == "none":
        posts = posts.filter(platform_posts__isnull=True)
    elif channel:
        posts = posts.filter(platform_posts__social_account_id=channel)
    posts = list(
        posts.select_related("workspace", "author", "jrc_production")
        # P1-B09: shotlist progress on the card ("3/7 done").
        .annotate(
            shotlist_total=Count("jrc_shotlist_items", distinct=True),
            shotlist_done=Count("jrc_shotlist_items", filter=Q(jrc_shotlist_items__is_done=True), distinct=True),
        )
        .prefetch_related(
            Prefetch("platform_posts", queryset=PlatformPost.objects.select_related("social_account__jrc_channel"))
        )
        .order_by("-updated_at", "id")
    )
    comment_counts = _visible_comment_counts(posts, request.user)
    columns = {value: {"value": value, "label": label, "cards": []} for value, label in PipelineStage.choices}
    waiting = []
    for post in posts:
        membership = by_workspace[str(post.workspace_id)]
        if is_waiting_for(post, membership):
            waiting.append(post)
        card = _board_card(post, membership, comment_counts.get(post.pk, 0))
        columns[card["stage"]]["cards"].append(card)
    for column in columns.values():
        column["cards"] = _sort_cards(column["cards"], newest_first=column["value"] == PipelineStage.PUBLISHED)
    creatable = [m.workspace for m in memberships if can_create_content(m)]
    creatable_ids = {str(client.id) for client in creatable}
    idea_client = selected if selected in creatable_ids else request.session.get(IDEA_CLIENT_KEY, "")
    if idea_client not in creatable_ids:
        idea_client = str(creatable[0].id) if creatable else ""
    creation_channels = (
        SocialAccount.objects.filter(workspace__in=creatable, jrc_channel__is_enabled=True)
        .select_related("workspace", "jrc_channel")
        .order_by("workspace__name", "account_name")
    )
    return {
        "columns": list(columns.values()),
        "waiting_for_you": waiting,
        "clients": [m.workspace for m in memberships],
        "creatable_clients": creatable,
        "creation_channels": creation_channels,
        "selected_client": selected,
        "selected_channel": channel,
        # Client names only help when the cards on screen belong to more than one client.
        "show_client": len({post.workspace_id for post in posts}) > 1,
        "idea_client": idea_client,
        "channels": channels,
        "total": len(posts),
        "error": error,
        "new_title": title,
    }


@login_required
@require_GET
def board(request):
    try:
        context = _board_context(request)
    except ValidationError as exc:
        return HttpResponseBadRequest(" ".join(exc.messages))
    return render(request, "jrc_production/board.html", context)


@login_required
@require_POST
def create(request):
    # Resolve against the current user's memberships before parsing a submitted UUID.
    memberships = {str(m.workspace_id): m for m in _memberships(request.user)}
    membership = memberships.get(request.POST.get("workspace", ""))
    if membership is None:
        raise Http404
    title = request.POST.get("title", "").strip()
    try:
        create_content(membership.workspace, title, request.user, social_account_ids=request.POST.getlist("channels"))
    except ValidationError as exc:
        return render(
            request,
            "jrc_production/board.html",
            _board_context(
                request,
                error=" ".join(exc.messages),
                title=title,
                selected_client=membership.workspace_id,
            ),
            status=400,
        )
    messages.success(request, "Content added to Idea.")
    return redirect(reverse("jrc_production:board") + f"?client={membership.workspace_id}")


@login_required
@require_POST
def quick_idea(request):
    """Save a one-line idea without channel or date; the board stays open for the next one."""
    memberships = {str(m.workspace_id): m for m in _memberships(request.user)}
    membership = memberships.get(request.POST.get("workspace", ""))
    if membership is None:
        raise Http404
    in_place = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    board_client = request.POST.get("board_client", "")
    board_client = board_client if board_client in memberships else ""
    board_channel = request.POST.get("board_channel", "")
    board_channel = board_channel if board_channel == "none" or _UUID_RE.match(board_channel) else ""
    try:
        post = create_content(membership.workspace, request.POST.get("title", ""), request.user)
    except ValidationError as exc:
        error = " ".join(exc.messages)
        if in_place:
            return HttpResponseBadRequest(error, content_type="text/plain; charset=utf-8")
        messages.error(request, error)
        return redirect(reverse("jrc_production:board") + "?" + urlencode({"client": board_client}))
    request.session[IDEA_CLIENT_KEY] = str(membership.workspace_id)
    if not in_place:
        messages.success(request, "Idea saved.")
        return redirect(
            reverse("jrc_production:board") + "?" + urlencode({"client": board_client, "channel": board_channel})
        )
    post = (
        Post.objects.select_related("workspace", "jrc_production")
        .annotate(
            shotlist_total=Count("jrc_shotlist_items", distinct=True),
            shotlist_done=Count("jrc_shotlist_items", filter=Q(jrc_shotlist_items__is_done=True), distinct=True),
        )
        .prefetch_related(
            Prefetch("platform_posts", queryset=PlatformPost.objects.select_related("social_account__jrc_channel"))
        )
        .get(pk=post.pk)
    )
    context = {
        "card": _board_card(post, membership),
        "selected_client": board_client,
        "selected_channel": board_channel,
    }
    response = render(request, "jrc_production/_board_card.html", context, status=201)
    # A new idea has no channel; it belongs on the open board only if the active filters include it.
    matches = board_client in ("", str(membership.workspace_id)) and board_channel in ("", "none")
    response["X-Board-Match"] = "1" if matches else "0"
    return response


@login_required
@require_POST
def move(request, workspace_id, post_id):
    workspace = get_object_or_404(Workspace, pk=workspace_id, is_archived=False)
    post = get_object_or_404(Post, pk=post_id, workspace=workspace)
    try:
        scheduled_at = None
        if request.POST.get("scheduled_at"):
            try:
                scheduled_at = parse_datetime(request.POST["scheduled_at"])
            except ValueError:
                scheduled_at = None
            if scheduled_at is not None and timezone.is_naive(scheduled_at):
                scheduled_at = timezone.make_aware(scheduled_at, ZoneInfo(workspace.effective_timezone))
        moved = move_content(
            post,
            request.POST.get("stage", ""),
            request.user,
            expected_stage=request.POST.get("expected_stage", ""),
            scheduled_at=scheduled_at,
        )
    except (ValidationError, ValueError) as exc:
        error = " ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
        messages.error(request, error)
    else:
        label = PipelineStage(pipeline_stage(moved)).label
        messages.success(request, f"Content moved to {label}.")
    # Preserve only validated board filter values, never redirect to caller-supplied URLs.
    filters = {key: request.POST.get(key, "") for key in ("client", "channel")}
    return redirect(reverse("jrc_production:board") + "?" + urlencode(filters))


# P1-B08: Concept (the idea worked out in versions) comes before the Script.
TABS = ("concept", "script", "shotlist", "video", "caption", "approval", "comments", "publish", "history")


def _default_tab(post):
    """A content without a script opens on its Concept (e.g. a quick idea); otherwise on the Script."""
    has_script = (
        TextVersion.objects.filter(document__post=post, document__kind=TextDocument.Kind.SCRIPT, state="final")
        .exclude(text="")
        .exists()
    )
    return "script" if has_script else "concept"


def _word_diff(previous, current):
    """Return safe, whitespace-preserving word diff fragments for a template."""

    def split_words(value):
        return re.findall(r"\w+|[^\w\s]+|\s+", value, flags=re.UNICODE)

    old_words, new_words = split_words(previous), split_words(current)
    old_fragments, new_fragments = [], []
    for operation, old_start, old_end, new_start, new_end in difflib.SequenceMatcher(
        a=old_words, b=new_words, autojunk=False
    ).get_opcodes():
        if operation in ("equal", "delete", "replace"):
            old_fragments.append({"text": "".join(old_words[old_start:old_end]), "changed": operation != "equal"})
        if operation in ("equal", "insert", "replace"):
            new_fragments.append({"text": "".join(new_words[new_start:new_end]), "changed": operation != "equal"})
    return old_fragments, new_fragments


def _enabled_caption_accounts(post):
    """Only explicitly attached channels; switched-off copies keep their stored work.

    Reading or saving a caption must never attach another client channel implicitly.
    """
    accounts = SocialAccount.objects.filter(workspace_id=post.workspace_id, platform_posts__post=post).select_related(
        "jrc_channel"
    )
    return [
        account
        for account in accounts.order_by("account_name")
        if getattr(getattr(account, "jrc_channel", None), "is_enabled", True)
    ]


def _caption_channels(post, submitted=None):
    channels = []
    variants = {variant.social_account_id: variant for variant in post.platform_posts.select_related("social_account")}
    for account in _enabled_caption_accounts(post):
        variant = variants.get(account.pk)
        # Accounts are already narrowed to copies explicitly attached to this content.
        key = str(variant.pk) if variant else f"new:{account.pk}"
        document = (
            TextDocument.objects.filter(platform_post=variant, kind=TextDocument.Kind.CAPTION).first()
            if variant
            else None
        )
        latest = (
            document.versions.filter(state=TextVersion.State.FINAL).order_by("-number").first() if document else None
        )
        previous = (
            document.versions.filter(state=TextVersion.State.FINAL, number__lt=latest.number)
            .order_by("-number")
            .first()
            if latest
            else None
        )
        saved_text = latest.text if latest else (variant.effective_caption if variant else (post.caption or ""))
        submitted_caption = (submitted or {}).get("caption") if (submitted or {}).get("platform_post") == key else None
        current_text = submitted_caption if submitted_caption is not None else saved_text
        old_diff, new_diff = _word_diff(previous.text, current_text) if previous else ([], [])
        preview = None
        if account.platform in ("instagram", "instagram_login"):
            # The preview always shows the last saved version, never an unsaved edit.
            count = account.caption_wire_length(saved_text)
            preview = {
                "account": account,
                "title": "",
                "caption": saved_text,
                "first_comment": "",
                "char_count": count,
                "char_limit": account.char_limit,
                "is_over_limit": count > account.char_limit,
                "truncated_caption": saved_text[: account.char_limit],
            }
        channels.append(
            {
                "key": key,
                "variant": variant,
                "account": account,
                "platform_label": account.get_platform_display(),
                "document": document,
                "latest": latest,
                "previous": previous,
                "text": current_text,
                "char_count": account.caption_wire_length(current_text),
                "char_limit": account.char_limit,
                "escaped_chars": account.escaped_chars,
                "old_diff": old_diff,
                "new_diff": new_diff,
                "previews": [preview] if preview else [],
                "reopens_review": bool(variant and edit_reopens_review(variant)),
            }
        )
    return channels


def _create_missing_copy(post, account_id):
    """Legacy caption/video forms may reference a copy, but never attach a new channel implicitly."""
    variant = post.platform_posts.filter(social_account_id=account_id).first()
    if variant is None:
        raise ValidationError("Add this channel to the content first.")
    return variant


@login_required
@require_http_methods(["GET", "POST"])
def content_detail(request, workspace_id, post_id):
    """Show a workspace member the shared content page and its audit history."""
    workspace = get_object_or_404(Workspace, pk=workspace_id)
    membership = WorkspaceMembership.objects.filter(user=request.user, workspace=workspace).first()
    if membership is None:
        raise Http404

    post = get_object_or_404(
        Post.objects.filter(workspace=workspace)
        .select_related("author", "category", "jrc_production__stage_changed_by")
        .prefetch_related("platform_posts__social_account"),
        pk=post_id,
    )
    active_tab = request.GET.get("tab", "")
    if active_tab not in TABS:
        active_tab = _default_tab(post)

    is_client = membership.workspace_role in (
        WorkspaceMembership.WorkspaceRole.CLIENT,
        WorkspaceMembership.WorkspaceRole.VIEWER,
    )
    can_edit_captions = text_services.can_edit_text(post, membership)
    caption_error = ""
    channel_error = ""
    date_form = None
    date_target = ""
    date_error = ""
    if request.method == "POST" and request.POST.get("action") == "set_date":
        if not can_schedule(membership):
            raise PermissionDenied("You cannot schedule content.")
        active_tab = "publish"
        try:
            variant = get_object_or_404(PlatformPost, pk=request.POST.get("platform_post"), post=post)
        except ValidationError:
            raise Http404 from None
        date_target = str(variant.pk)
        date_form = PublicationDateForm(request.POST, auto_id=f"date-{variant.pk}-%s")
        with timezone.override(ZoneInfo(workspace.effective_timezone)):
            valid = date_form.is_valid()
        if valid:
            try:
                set_publication_date(post, variant.pk, request.user, date_form.cleaned_data["scheduled_at"])
            except ValidationError as exc:
                date_error = " ".join(exc.messages)
            else:
                messages.success(request, "Publication date saved. Publish this channel manually at the planned time.")
                return redirect(f"{request.path}?tab=publish")
    elif request.method == "POST" and request.POST.get("action") in {"add_channel", "remove_channel"}:
        try:
            if request.POST["action"] == "add_channel":
                account = get_object_or_404(SocialAccount, pk=request.POST.get("channel"), workspace=workspace)
                add_content_channel(post, account.pk, request.user)
            else:
                variant = get_object_or_404(PlatformPost, pk=request.POST.get("platform_post"), post=post)
                remove_content_channel(post, variant.pk, request.user)
        except ValidationError as exc:
            channel_error = " ".join(exc.messages)
        else:
            messages.success(request, "Content channels updated.")
            return redirect(f"{request.path}?tab={active_tab}#content-channels")
    elif request.method == "POST":
        if request.POST.get("action") != "save_caption":
            return HttpResponseBadRequest("Unknown action.")
        if not can_edit_captions:
            raise PermissionDenied("You cannot edit captions for this content.")
        target = request.POST.get("platform_post", "")
        variant = None
        if not target.startswith("new:"):
            variant = get_object_or_404(
                PlatformPost.objects.select_related("post", "social_account"),
                pk=target,
                post=post,
            )
        # Textareas can submit Windows-style line endings even though the browser
        # counter treats every newline as one character. Store and count one form.
        caption = request.POST.get("caption", "").replace("\r\n", "\n").replace("\r", "\n")
        if variant is None:
            new_account = next((a for a in _enabled_caption_accounts(post) if f"new:{a.pk}" == target), None)
            if new_account is None:
                raise Http404("This channel is not enabled for this client.")
            limit_account = new_account
        else:
            limit_account = variant.social_account
        if limit_account.caption_wire_length(caption) > limit_account.char_limit:
            caption_error = f"Caption exceeds the {limit_account.char_limit}-character limit."
        else:
            try:
                with transaction.atomic():
                    Post.objects.select_for_update().get(pk=post.pk)
                    if variant is None:
                        variant = _create_missing_copy(post, target.removeprefix("new:"))
                    base_id = request.POST.get("base_version", "")
                    text_services.save_caption(
                        post.pk, variant.pk, request.user, caption, base_version_id=base_id or None
                    )
            except ConflictError as exc:
                caption_error = str(exc) or "This caption changed in another tab. Reload the page."
            except ValidationError as exc:
                caption_error = " ".join(exc.messages)
            else:
                was_approved = variant.status == PlatformPost.Status.APPROVED
                variant.refresh_from_db(fields=["status"])
                if was_approved and variant.status == PlatformPost.Status.PENDING_REVIEW:
                    messages.success(request, "Caption saved as a new version. It goes back to review for approval.")
                else:
                    messages.success(request, "Caption saved as a new version.")
                return redirect(f"{request.path}?tab=caption")

    caption_channels = _caption_channels(post, request.POST if request.method == "POST" else None)
    caption_media_items = []
    if active_tab == "caption" and any(item["previews"] for item in caption_channels):
        for attachment in post.media_attachments.select_related("media_asset"):
            asset = attachment.media_asset
            caption_media_items.append(
                {"url": asset.file.url if asset.file else "", "is_video": asset.is_video, "filename": asset.filename}
            )

    can_view_internal_notes = membership.workspace_role not in ("client", "viewer")
    comments = comment_services.get_comments_for_post(post, request.user)
    history = [
        {
            "kind": "stage",
            "created_at": change.created_at,
            "label": f"Moved from {change.get_from_stage_display()} to {change.get_to_stage_display()}",
            "actor": change.user,
            "note": change.note,
            "can_view_note": can_view_internal_notes or change.note_visibility == "external",
        }
        for change in post.jrc_stage_changes.select_related("user").all()
    ]
    approval_actions = list(ApprovalAction.objects.filter(post=post).select_related("user"))
    history.extend(
        {
            "kind": "approval",
            "created_at": action.created_at,
            "label": action.get_action_display(),
            "actor": action.user,
            "note": action.comment,
            "can_view_note": can_view_internal_notes or action.note_visibility == "external",
        }
        for action in approval_actions
    )
    history.extend(
        {
            "kind": "publication",
            "created_at": publication.published_at,
            "label": f"Marked {publication.platform_post.social_account.get_platform_display()} as published",
            "actor": publication.published_by,
            "note": publication.post_url,
            "can_view_note": True,
        }
        for publication in ManualPublication.objects.filter(platform_post__post=post).select_related(
            "published_by", "platform_post__social_account"
        )
    )
    history.extend(
        {
            "kind": "shotlist",
            "created_at": change.created_at,
            "label": _SHOTLIST_HISTORY_LABELS[change.action].format(text=change.item_text),
            "actor": change.user,
            "note": "",
            "can_view_note": True,
        }
        for change in post.jrc_shotlist_changes.select_related("user").all()
    )
    from apps.jrc_video.models import VideoActivity, VideoVersion

    history.extend(
        {
            "kind": "video",
            "created_at": version.uploaded_at,
            "label": f"Uploaded video v{version.number} · {version.video.platform_post.social_account.account_name}",
            "actor": version.uploaded_by,
            "note": "",
            "can_view_note": True,
        }
        for version in VideoVersion.objects.filter(video__platform_post__post=post)
        .exclude(status=VideoVersion.Status.UPLOADING)
        .select_related("uploaded_by", "video__platform_post__social_account")
    )
    history.extend(
        {
            "kind": "video",
            "created_at": event.created_at,
            "label": f"{event.get_action_display()} · v{event.version.number} · {event.version.video.platform_post.social_account.account_name}",
            "actor": event.actor,
            "note": "",
            "can_view_note": True,
        }
        for event in VideoActivity.objects.filter(version__video__platform_post__post=post).select_related(
            "actor", "version__video__platform_post__social_account"
        )
    )
    history.extend(
        {
            "kind": "text",
            "created_at": version.finalized_at,
            "label": (
                f"Saved {version.document.get_kind_display()} version {version.number}"
                + (
                    f" · {version.document.platform_post.social_account.get_platform_display()}"
                    f" · {version.document.platform_post.social_account.account_name}"
                    if version.document.platform_post_id
                    else ""
                )
                + (" · Empty text" if not version.text.strip() else "")
            ),
            "actor": version.created_by,
            "note": "",
            "can_view_note": True,
        }
        for version in TextVersion.objects.filter(document__post=post, state=TextVersion.State.FINAL)
        .defer("content")
        .select_related("created_by", "document__platform_post__social_account")
    )
    history.sort(key=lambda item: item["created_at"], reverse=True)

    video_context = {}
    if active_tab == "video" and post.platform_posts.exists():
        # F5-B05 / F5-B08: video versions and the embedded player per channel.
        from apps.jrc_video import uploads as video_uploads
        from apps.jrc_video.models import VideoAccount, VideoProject

        video_uploads.refresh_on_open(request.user, post)
        video_context = {
            "video_cards": video_uploads.channel_cards(request.user, post, membership),
            "video_accept": video_uploads.allowed_extensions_attr(),
            "video_max_bytes": video_uploads.max_upload_bytes(),
            # Without a synchronized project and account the sign-in would fail: say so instead.
            "video_player_ready": VideoProject.objects.filter(workspace=workspace).exists()
            and VideoAccount.objects.filter(user=request.user).exists(),
        }
    approval_ctx = {}
    if active_tab == "approval":
        approval_ctx = approval_context(
            post,
            membership,
            _enabled_caption_accounts(post),
            can_view_internal_notes=can_view_internal_notes,
        )
    shotlist_ctx = {}
    if active_tab == "shotlist":
        shotlist_ctx = _shotlist_context(post, membership, focus=request.GET.get("focus", ""))
    publish_channels = []
    can_publish = can_publish_directly(membership)
    if active_tab == "publish":
        from apps.jrc_video.models import VideoAsset, VideoVersion

        variants = {item.social_account_id: item for item in post.platform_posts.select_related("social_account").all()}
        videos = {
            item.platform_post_id: item
            for item in VideoAsset.objects.filter(platform_post__post=post).select_related("final_version")
        }
        publications = {
            item.platform_post_id: item for item in ManualPublication.objects.filter(platform_post__post=post)
        }
        for account in _enabled_caption_accounts(post):
            variant = variants.get(account.pk)
            if variant is None:
                continue
            video = videos.get(variant.pk)
            publication = publications.get(variant.pk)
            document = TextDocument.objects.filter(platform_post=variant, kind=TextDocument.Kind.CAPTION).first()
            latest_caption = document.versions.filter(state="final").order_by("-number").first() if document else None
            caption = latest_caption.text if latest_caption else (variant.effective_caption or "")
            scheduled_at = variant.scheduled_at
            if scheduled_at:
                scheduled_at = timezone.localtime(scheduled_at, ZoneInfo(workspace.effective_timezone or "UTC"))
            video_final = bool(
                video and video.final_version_id and video.final_version.status == VideoVersion.Status.READY
            )
            # P1-B27: the approval workflow, caption and final video come from the shared rule, both for
            # what is offered and for the reasons shown next to an unavailable action.
            schedule_blocker = publication_blocker(variant)
            may_schedule = (
                can_schedule(membership)
                and getattr(getattr(account, "jrc_channel", None), "is_enabled", False)
                and account.jrc_channel.kind in {"manual", "custom"}
            )
            missing = missing_for_publication(variant, caption=caption, video=video)
            download_missing = missing_for_publication(variant, caption=caption, video=video, download=True)
            publish_channels.append(
                {
                    "variant": variant,
                    "label": f"{account.get_platform_display()} · {account.account_name}",
                    "caption": caption,
                    "scheduled_at": scheduled_at,
                    "scheduled_label": (scheduled_at.strftime("%b %-d, %Y · %-I:%M %p %Z") if scheduled_at else ""),
                    "can_schedule": may_schedule and not schedule_blocker,
                    "schedule_blocker": schedule_blocker if may_schedule and publication is None else "",
                    "date_open": date_target == str(variant.pk),
                    "date_error": date_error if date_target == str(variant.pk) else "",
                    "date_form": date_form
                    if date_target == str(variant.pk)
                    else PublicationDateForm(
                        initial={"scheduled_at": scheduled_at.strftime("%Y-%m-%dT%H:%M") if scheduled_at else ""},
                        auto_id=f"date-{variant.pk}-%s",
                    ),
                    "video_final": video_final,
                    "video_number": video.final_version.number if video_final else None,
                    "has_video": bool(video),
                    "published": publication is not None,
                    "publication_url": publication.post_url if publication else "",
                    "can_mark": can_publish and not publication_blocker(variant) and not missing,
                    "can_publish": can_publish,
                    "missing": missing,
                    "download_missing": download_missing,
                    "download_url": reverse(
                        "jrc_video:final_download",
                        kwargs={"workspace_id": workspace.id, "post_id": post.id, "platform_post_id": variant.id},
                    )
                    if can_download_final(membership, variant, caption=caption, video=video)
                    else "",
                }
            )
    return render(
        request,
        "jrc_production/content_detail.html",
        {
            **video_context,
            **approval_ctx,
            **shotlist_ctx,
            "workspace": workspace,
            "post": post,
            "pipeline_stage": pipeline_stage(post),
            "active_tab": active_tab,
            "tabs": TABS,
            "history": history[:100],
            "empty_initial_text": TextVersion.objects.filter(
                document__post=post, document__kind=active_tab, state=TextVersion.State.FINAL, number=1, text=""
            ).exists()
            if active_tab in {"concept", "script"}
            else False,
            "can_view_internal_notes": can_view_internal_notes,
            "can_edit_posts": text_services.can_edit_text(post, membership),
            "comments": comments,
            "can_resolve_comments": can_moderate_comments(membership),
            "can_comment": can_comment(membership),
            "comment_visibility_default": (
                PostComment.Visibility.EXTERNAL if is_client else PostComment.Visibility.INTERNAL
            ),
            "is_client": is_client,
            "can_edit_captions": can_edit_captions,
            "can_manage_channels": can_manage_channels(post, membership),
            "channel_error": channel_error,
            "addable_channels": SocialAccount.objects.filter(
                workspace=workspace,
                jrc_channel__is_enabled=True,
            )
            .exclude(platform_posts__post=post)
            .select_related("jrc_channel")
            .order_by("account_name"),
            "needs_channel": not post.platform_posts.exists() and active_tab in {"video", "caption", "publish"},
            "caption_channels": caption_channels,
            "caption_media_items": caption_media_items,
            "caption_error": caption_error,
            "publish_channels": publish_channels,
            "can_publish": can_publish,
        },
    )


_SHOTLIST_HISTORY_LABELS = {
    ShotlistChange.Action.ADDED: 'Added "{text}" to the shotlist',
    ShotlistChange.Action.DONE: 'Checked off "{text}" in the shotlist',
    ShotlistChange.Action.UNDONE: 'Unchecked "{text}" in the shotlist',
    ShotlistChange.Action.DELETED: 'Removed "{text}" from the shotlist',
}


def _shotlist_context(post, membership, *, focus="", error="", new_text="", new_note=""):
    # The automatic item must exist even for content created before P1-B09 outside the migration.
    shotlist_services.ensure_thumbnail_item(post)
    items = shotlist_services.items_for(post)
    done, total = shotlist_services.progress(items)
    return {
        "shotlist_items": items,
        "shotlist_done": done,
        "shotlist_total": total,
        "can_edit_shotlist": shotlist_services.can_edit_shotlist(post, membership),
        "shotlist_focus": focus,
        "shotlist_error": error,
        "shotlist_new_text": new_text,
        "shotlist_new_note": new_note,
        "shotlist_max_text": shotlist_services.MAX_TEXT,
        "shotlist_max_note": shotlist_services.MAX_NOTE,
    }


@login_required
@require_POST
def shotlist_action(request, workspace_id, post_id):
    """Add, edit, check off, reorder or delete a shotlist item (P1-B09).

    A plain form post redirects back to the tab. The tab's script sends ``X-JRC-Shotlist: 1`` and gets
    the re-rendered list instead, so ticking items on a phone does not reload the page.
    """
    workspace = get_object_or_404(Workspace, pk=workspace_id)
    membership = WorkspaceMembership.objects.filter(user=request.user, workspace=workspace).first()
    if membership is None:
        raise Http404
    post = get_object_or_404(Post.objects.select_related("workspace"), pk=post_id, workspace=workspace)
    action = request.POST.get("action", "")
    item_id = request.POST.get("item", "")
    text, note = request.POST.get("text"), request.POST.get("note")
    focus, error, status = "", "", 200
    try:
        if action == "add":
            shotlist_services.add_item(post, request.user, text or "", note or "")
            focus = "add"
        elif action == "update":
            shotlist_services.update_item(post, item_id, request.user, text=text, note=note)
            focus = f"item-{item_id}"
        elif action == "done":
            shotlist_services.update_item(post, item_id, request.user, is_done=request.POST.get("done") == "1")
            focus = f"item-{item_id}"
        elif action in ("up", "down"):
            shotlist_services.move_item(post, item_id, request.user, offset=-1 if action == "up" else 1)
            focus = f"item-{item_id}"
        elif action == "delete":
            shotlist_services.delete_item(post, item_id, request.user)
            focus = "add"
        else:
            return HttpResponseBadRequest("Unknown action.")
    except shotlist_services.ItemNotFoundError as exc:
        error, status = str(exc), 409
    except ValidationError as exc:
        error, status = " ".join(exc.messages), 400
    if request.headers.get("X-JRC-Shotlist") == "1":
        context = _shotlist_context(
            post,
            membership,
            focus=focus,
            error=error,
            new_text=(text or "") if error and action == "add" else "",
            new_note=(note or "") if error and action == "add" else "",
        )
        return render(
            request, "jrc_production/_shotlist.html", {**context, "workspace": workspace, "post": post}, status=status
        )
    if error:
        messages.error(request, error)
    url = reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.id, "post_id": post.id})
    if focus == "add" and not error:
        # No fragment here: browsers skip autofocus when the URL targets an element.
        return redirect(f"{url}?tab=shotlist&focus=add")
    anchor = f"#shotlist-{focus}" if focus and not error else "#shotlist"
    return redirect(f"{url}?tab=shotlist{anchor}")


@login_required
@require_POST
def mark_published(request, workspace_id, post_id):
    workspace = get_object_or_404(Workspace, pk=workspace_id)
    post = get_object_or_404(Post, pk=post_id, workspace=workspace)
    try:
        mark_published_manually(
            post,
            request.POST.get("platform_post", ""),
            request.user,
            post_url=request.POST.get("post_url", ""),
        )
    except (PermissionDenied, ValidationError) as exc:
        if isinstance(exc, PermissionDenied):
            raise
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, "Channel marked as published.")
    return redirect(
        f"{reverse('jrc_production:content_detail', kwargs={'workspace_id': workspace.id, 'post_id': post.id})}?tab=publish"
    )


def _comment_post_for_member(request, workspace_id, post_id):
    workspace = get_object_or_404(Workspace, pk=workspace_id)
    membership = WorkspaceMembership.objects.filter(user=request.user, workspace=workspace).first()
    if membership is None:
        raise PermissionDenied("You are not a member of this workspace.")
    post = get_object_or_404(Post, pk=post_id, workspace=workspace)
    return workspace, post


def _comments_redirect(workspace, post):
    return redirect(
        reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.id, "post_id": post.id})
        + "?tab=comments"
    )


@login_required
@require_POST
def add_content_comment(request, workspace_id, post_id):
    workspace, post = _comment_post_for_member(request, workspace_id, post_id)
    body = request.POST.get("body", "").strip()
    if not body:
        messages.error(request, "Write a comment before sending it.")
    else:
        try:
            comment_services.create_comment(
                post=post,
                author=request.user,
                body=body,
                visibility=request.POST.get("visibility"),
            )
        except (ValueError, PermissionError) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Comment added.")
    return _comments_redirect(workspace, post)


@login_required
@require_POST
def reply_to_content_comment(request, workspace_id, post_id, comment_id):
    workspace, post = _comment_post_for_member(request, workspace_id, post_id)
    body = request.POST.get("body", "").strip()
    if not body:
        messages.error(request, "Write a reply before sending it.")
    else:
        try:
            comment_services.create_comment(
                post=post,
                author=request.user,
                body=body,
                parent_id=comment_id,
            )
        except (ValueError, PermissionError) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Reply added.")
    return _comments_redirect(workspace, post)


@login_required
@require_POST
def resolve_content_comment(request, workspace_id, post_id, comment_id):
    workspace, post = _comment_post_for_member(request, workspace_id, post_id)
    try:
        comment_services.resolve_comment(
            comment_id,
            request.user,
            workspace,
            resolved=request.POST.get("resolved") == "true",
        )
    except PermissionError as exc:
        raise PermissionDenied(str(exc)) from exc
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Comment thread updated.")
    return _comments_redirect(workspace, post)


@login_required
@require_POST
def approval_decision(request, workspace_id, post_id):
    """Approve the content or ask for changes from the Approval tab (F7-B02).

    Both decisions authorize on the server (``move_content`` / ``request_content_changes``);
    the buttons on the page only mirror ``can_decide``.
    """
    workspace, post = _comment_post_for_member(request, workspace_id, post_id)
    decision = request.POST.get("decision", "")
    expected_stage = request.POST.get("expected_stage", "")
    try:
        if decision == "approve":
            moved = move_content(post, PipelineStage.APPROVED, request.user, expected_stage=expected_stage)
            if pipeline_stage(moved) == PipelineStage.APPROVED:
                messages.success(request, "Approved. Thank you.")
            else:
                messages.success(request, "Approved by the team. It now waits for the client's approval.")
        elif decision == "request_changes":
            request_content_changes(post, request.user, request.POST.get("comment", ""), expected_stage=expected_stage)
            messages.success(request, "Changes requested. The team can see your comment.")
        else:
            return HttpResponseBadRequest("Unknown decision.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect(
        reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.id, "post_id": post.id})
        + "?tab=approval"
    )
