import difflib
import re
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Prefetch
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_POST

from apps.approvals.models import ApprovalAction
from apps.composer.models import PlatformPost, Post
from apps.jrc_texts import services as text_services
from apps.jrc_texts.models import TextDocument
from apps.jrc_texts.services import ConflictError
from apps.members.models import WorkspaceMembership
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace

from .models import PipelineStage
from .services import available_moves, can_create_content, create_content, move_content, pipeline_stage


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
    for post in posts:
        stage = pipeline_stage(post)
        moves = available_moves(post, by_workspace[str(post.workspace_id)])
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
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied("You do not have access to this client.")
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


TABS = ("script", "video", "caption", "approval", "history")


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


def _caption_channels(post, submitted=None):
    from apps.jrc_texts.models import TextVersion

    channels = []
    variants = post.platform_posts.select_related("social_account").order_by("social_account__account_name")
    for variant in variants:
        document = TextDocument.objects.filter(platform_post=variant, kind=TextDocument.Kind.CAPTION).first()
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
        submitted_caption = (
            (submitted or {}).get("caption") if (submitted or {}).get("platform_post") == str(variant.pk) else None
        )
        current_text = (
            submitted_caption
            if submitted_caption is not None
            else (latest.text if latest else variant.effective_caption)
        )
        old_diff, new_diff = _word_diff(previous.text, current_text) if previous else ([], [])
        account = variant.social_account
        channels.append(
            {
                "variant": variant,
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
            }
        )
    return channels


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

    can_edit_captions = membership.effective_permissions.get("create_posts", False)
    caption_error = ""
    if request.method == "POST":
        if request.POST.get("action") != "save_caption":
            return HttpResponseBadRequest("Unknown action.")
        variant = get_object_or_404(
            PlatformPost.objects.select_related("post", "social_account"),
            pk=request.POST.get("platform_post"),
            post=post,
        )
        if not can_edit_captions or (
            post.author_id != request.user.pk and not membership.effective_permissions.get("edit_others_posts", False)
        ):
            raise PermissionDenied("You cannot edit captions for this content.")
        caption = request.POST.get("caption", "")
        if variant.social_account.caption_wire_length(caption) > variant.social_account.char_limit:
            caption_error = f"Caption exceeds the {variant.social_account.char_limit}-character limit."
        else:
            try:
                with transaction.atomic():
                    Post.objects.select_for_update().get(pk=post.pk)
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
            "can_edit_captions": can_edit_captions,
            "caption_channels": _caption_channels(post, request.POST if request.method == "POST" else None),
            "caption_error": caption_error,
        },
    )
