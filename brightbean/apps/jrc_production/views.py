import difflib
import re
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Prefetch
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.approvals import comments as comment_services
from apps.approvals.models import ApprovalAction, PostComment
from apps.composer.models import PlatformPost, Post
from apps.jrc_texts import services as text_services
from apps.jrc_texts.models import TextDocument
from apps.jrc_texts.services import ConflictError
from apps.members.models import WorkspaceMembership
from apps.members.permissions import can_publish_directly
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace

from .approval import approval_context, is_waiting_for
from .models import ManualPublication, PipelineStage
from .services import (
    available_moves,
    can_create_content,
    create_content,
    mark_published_manually,
    move_content,
    pipeline_stage,
    request_content_changes,
)


def _memberships(user):
    return list(
        WorkspaceMembership.objects.filter(user=user, workspace__is_archived=False)
        .select_related("workspace", "custom_role")
        .order_by("workspace__name")
    )


def _board_context(request, *, error="", title="", selected_client=None):
    memberships = _memberships(request.user)
    by_workspace = {str(m.workspace_id): m for m in memberships}
    selected = request.GET.get("client", "") if selected_client is None else str(selected_client)
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
    if channel and channel not in {str(c.pk) for c in channels}:
        raise ValidationError("Choose a channel for the selected client.")
    posts = Post.objects.filter(workspace_id__in=workspace_ids)
    if channel:
        posts = posts.filter(platform_posts__social_account_id=channel)
    posts = (
        posts.select_related("workspace", "author", "jrc_production")
        .prefetch_related(
            Prefetch("platform_posts", queryset=PlatformPost.objects.select_related("social_account__jrc_channel"))
        )
        .order_by("-updated_at", "id")
    )
    columns = {value: {"value": value, "label": label, "cards": []} for value, label in PipelineStage.choices}
    total = 0
    waiting = []
    for post in posts:
        stage = pipeline_stage(post)
        membership = by_workspace[str(post.workspace_id)]
        moves = available_moves(post, membership)
        if is_waiting_for(post, membership):
            waiting.append(post)
        columns[stage]["cards"].append(
            {
                "post": post,
                "stage": stage,
                "moves": [(value, PipelineStage(value).label) for value in moves],
                "client_review": post.platform_posts.all()
                and any(pp.status == "pending_client" for pp in post.platform_posts.all()),
            }
        )
        total += 1
    creatable = [m.workspace for m in memberships if can_create_content(m)]
    return {
        "columns": list(columns.values()),
        "waiting_for_you": waiting,
        "clients": [m.workspace for m in memberships],
        "creatable_clients": creatable,
        "selected_client": selected,
        "selected_channel": channel,
        "channels": channels,
        "total": total,
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
        create_content(membership.workspace, title, request.user)
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
    from urllib.parse import urlencode

    filters = {key: request.POST.get(key, "") for key in ("client", "channel")}
    return redirect(reverse("jrc_production:board") + "?" + urlencode(filters))


TABS = ("script", "video", "caption", "approval", "publish", "comments", "history")


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
    """Accounts whose caption belongs on the card: enabled channels of this client (F6-B02).

    Channels switched off in the client profile are hidden in MVP1, but their copies and
    saved versions stay in the database. An account without a JR Content profile is kept
    so that content imported from Brightbean does not lose its caption field.
    """
    accounts = SocialAccount.objects.filter(workspace_id=post.workspace_id).select_related("jrc_channel")
    return [
        account
        for account in accounts.order_by("account_name")
        if getattr(getattr(account, "jrc_channel", None), "is_enabled", True)
    ]


def _caption_channels(post, submitted=None):
    from apps.jrc_texts.models import TextVersion

    channels = []
    variants = {variant.social_account_id: variant for variant in post.platform_posts.select_related("social_account")}
    for account in _enabled_caption_accounts(post):
        variant = variants.get(account.pk)
        # A channel enabled after the content was created has no copy yet. The field is
        # ready; the copy is created by the first save (see _create_missing_copy).
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
            }
        )
    return channels


def _create_missing_copy(post, account_id):
    """Create the draft copy for an enabled channel that was added after the content (F6-B02).

    Only while every existing copy is still a draft, so the new copy cannot change the
    content's stage. Runs inside the caller's transaction with the post row locked.
    """
    account = next((a for a in _enabled_caption_accounts(post) if str(a.pk) == account_id), None)
    if account is None:
        raise Http404("This channel is not enabled for this client.")
    existing = PlatformPost.objects.filter(post=post, social_account=account).first()
    if existing:
        return existing
    if post.platform_posts.exclude(status=PlatformPost.Status.DRAFT).exists():
        raise ValidationError(
            f"The {account.get_platform_display()} channel was enabled after this content left drafting, "
            "so it has no copy of this content yet."
        )
    return PlatformPost.objects.create(post=post, social_account=account, status=PlatformPost.Status.DRAFT)


@login_required
@require_http_methods(["GET", "POST"])
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

    is_client = membership.workspace_role in (
        WorkspaceMembership.WorkspaceRole.CLIENT,
        WorkspaceMembership.WorkspaceRole.VIEWER,
    )
    can_edit_captions = text_services.can_edit_text(post, membership)
    caption_error = ""
    if request.method == "POST":
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
                    document = TextDocument.objects.filter(
                        platform_post=variant, kind=TextDocument.Kind.CAPTION
                    ).first()
                    if document is None:
                        document = text_services.create_document(
                            post.pk,
                            request.user,
                            kind=TextDocument.Kind.CAPTION,
                            platform_post_id=variant.pk,
                            format=TextDocument.Format.PLAIN,
                            content={"text": variant.effective_caption},
                        )
                        base = document.versions.filter(state="final").order_by("-number").first()
                    else:
                        base_id = request.POST.get("base_version", "")
                        base = document.versions.filter(pk=base_id, state="final").first()
                        if base is None:
                            raise ConflictError("This caption changed in another tab. Reload the page.")
                    draft = text_services.edit(base.pk, request.user)
                    saved = text_services.save(draft.pk, request.user, expected_revision=0, content={"text": caption})
                    variant.platform_specific_caption = saved.text
                    variant.save(update_fields=["platform_specific_caption", "updated_at"])
            except ConflictError as exc:
                caption_error = str(exc) or "This caption changed in another tab. Reload the page."
            except ValidationError as exc:
                caption_error = " ".join(exc.messages)
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
    history.sort(key=lambda item: item["created_at"], reverse=True)

    video_context = {}
    if active_tab == "video":
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
            publish_channels.append(
                {
                    "variant": variant,
                    "label": f"{account.get_platform_display()} · {account.account_name}",
                    "caption": caption,
                    "scheduled_at": scheduled_at,
                    "scheduled_label": (scheduled_at.strftime("%b %-d, %Y · %-I:%M %p %Z") if scheduled_at else ""),
                    "video_final": video_final,
                    "has_video": bool(video),
                    "published": publication is not None,
                    "publication_url": publication.post_url if publication else "",
                    "can_mark": can_publish
                    and variant.status in {PlatformPost.Status.APPROVED, PlatformPost.Status.SCHEDULED}
                    and bool(caption.strip())
                    and (not video or video_final),
                    "download_url": reverse(
                        "jrc_video:final_download",
                        kwargs={"workspace_id": workspace.id, "post_id": post.id, "platform_post_id": variant.id},
                    )
                    if video_final and can_publish
                    else "",
                }
            )
    return render(
        request,
        "jrc_production/content_detail.html",
        {
            **video_context,
            **approval_ctx,
            "workspace": workspace,
            "post": post,
            "pipeline_stage": pipeline_stage(post),
            "active_tab": active_tab,
            "tabs": TABS,
            "history": history[:100],
            "can_view_internal_notes": can_view_internal_notes,
            "can_edit_posts": text_services.can_edit_text(post, membership),
            "comments": comments,
            "can_resolve_comments": not is_client and membership.effective_permissions.get("approve_posts", False),
            "comment_visibility_default": (
                PostComment.Visibility.EXTERNAL if is_client else PostComment.Visibility.INTERNAL
            ),
            "is_client": is_client,
            "can_edit_captions": can_edit_captions,
            "caption_channels": caption_channels,
            "caption_media_items": caption_media_items,
            "caption_error": caption_error,
            "publish_channels": publish_channels,
            "can_publish": can_publish,
        },
    )


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
