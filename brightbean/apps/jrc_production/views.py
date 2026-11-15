import difflib
import json
import re
from datetime import datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import add_never_cache_headers, patch_vary_headers
from django.utils.dateformat import format as date_format
from django.utils.dateparse import parse_datetime
from django.utils.html import format_html
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.approvals import comments as comment_services
from apps.approvals.edits import review_return_hint, review_return_notice
from apps.approvals.models import ApprovalAction, PostComment
from apps.composer.models import PlatformPost, Post
from apps.composer.output_formats import OutputFormat
from apps.jrc_texts import services as text_services
from apps.jrc_texts.editor_colors import TEXT_COLORS
from apps.jrc_texts.models import TextDocument, TextVersion, TextVersionNameChange
from apps.jrc_texts.services import ConflictError
from apps.jrc_video.downloads import can_download_final, missing_for_publication
from apps.members.models import WorkspaceMembership
from apps.members.permissions import (
    can_comment,
    can_delete_content,
    can_moderate_comments,
    can_publish_directly,
    can_schedule,
)
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace

from . import shotlist as shotlist_services
from .approval import approval_context, is_waiting_for
from .forms import ProposedDateForm, PublicationDateForm
from .models import (
    ContentCategoryChange,
    ContentProposalChange,
    ContentRename,
    ManualPublication,
    PipelineStage,
    ProductionStage,
    ShotlistChange,
)
from .services import (
    add_content_channel,
    addable_outputs,
    available_moves,
    can_change_proposal,
    can_create_content,
    can_decide,
    can_manage_channels,
    can_rename_content,
    change_category,
    change_proposed_date,
    content_membership,
    create_content,
    initial_stages,
    mark_published_manually,
    move_content,
    parse_output_choice,
    pipeline_stage,
    publication_blocker,
    remove_content_channel,
    rename_content,
    request_content_changes,
    set_publication_date,
)

BOARD_QUERY_KEY = "jrc_board_query"
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _memberships(user):
    return list(
        WorkspaceMembership.objects.filter(user=user, workspace__is_archived=False)
        .select_related("workspace", "custom_role")
        .order_by("workspace__name")
    )


def current_membership(request):
    """The membership in the current workspace, the client the menu shows (P2-B13).

    One workspace = one client: the board, New idea, New content and the quick idea read and
    write only here. ``RBACMiddleware`` resolves it (the workspace of the address, else the last
    one opened, else the earliest joined, the rule MCP uses too); None when the user works in no client.
    """
    membership = getattr(request, "workspace_membership", None)
    if membership is None or membership.workspace.is_archived:
        return None
    return membership


def open_workspace(request, workspace_id):
    """Make ``workspace_id`` current, as choosing it in the menu does (P2-B13).

    Global pages (the board, New content) accept ``?client=<workspace>`` from older links and from
    links that name the client they belong to. A workspace the user is not a member of is
    reported as missing. The choice is stored by ``RBACMiddleware`` once the page was shown.
    """
    if not _UUID_RE.match(str(workspace_id)):
        raise Http404
    current = current_membership(request)
    if current is not None and str(current.workspace_id) == str(workspace_id):
        return current
    membership = (
        WorkspaceMembership.objects.filter(
            user=request.user,
            workspace_id=workspace_id,
            workspace__is_archived=False,
            workspace__organization__deleted_at__isnull=True,
        )
        .select_related("workspace__organization", "custom_role")
        .first()
    )
    if membership is None:
        raise Http404
    request.workspace = membership.workspace
    request.workspace_membership = membership
    request.org = membership.workspace.organization
    request.org_membership = next(
        (m for m in getattr(request, "organization_memberships", []) if m.organization_id == request.org.id), None
    )
    request.navigated_workspace_membership = membership
    return membership


def _refuse_other_workspace(request, membership):
    """Writes go to the current workspace only (P2-B13, AK2); return the refusal or None.

    The form names the workspace it was shown for. If the menu switched to another client in the
    meantime (another tab), nothing is created rather than content landing at the wrong client.
    """
    current = current_membership(request)
    if current is not None and current.workspace_id == membership.workspace_id:
        return None
    name = current.workspace.name if current else "no client"
    return (
        f"This form was opened for {membership.workspace.name}, but the current client is now {name}. "
        "Nothing was saved. Reload the page to continue."
    )


def _refusal_response(request, refused, reload_url):
    """409 for a refused write, for fetch calls (plain text) and ordinary form posts (a page with the notice)."""
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return HttpResponse(refused, status=409, content_type="text/plain; charset=utf-8")
    return render(request, "jrc_production/refused.html", {"message": refused, "reload_url": reload_url}, status=409)


def _board_url(request):
    """The board of the page's client, with the channel filter it was last shown with (P2-B03, P2-B13).

    The page's workspace may differ from the board shown last (a notification opened another
    client's content): the link then returns to that client's board, without the old filter.
    """
    board = reverse("jrc_production:board")
    current = current_membership(request)
    if current is None:
        return board
    params = {"client": str(current.workspace_id)}
    last = QueryDict(request.session.get(BOARD_QUERY_KEY, ""))
    if last.get("client") == params["client"] and last.get("channel"):
        params["channel"] = last["channel"]
    return f"{board}?{urlencode(params)}"


def _visible_comment_counts(posts, user):
    """Count comments the viewer may read (clients see external threads only)."""
    by_workspace = {}
    for post in posts:
        by_workspace.setdefault(post.workspace_id, []).append(post)
    counts = {}
    for group in by_workspace.values():
        labels = {post.pk: set() for post in group}
        pending = list(comment_services.get_visible_comments(group, user, group[0].workspace))
        while pending:
            comment = pending.pop()
            counts[comment.post_id] = counts.get(comment.post_id, 0) + 1
            if comment.version_label:
                labels[comment.post_id].add(comment.version_label)
            pending.extend(comment.visible_replies)
        for post in group:
            post.comment_version_labels = sorted(labels[post.pk])
    return counts


def _board_card(post, membership, comment_count=0):
    from apps.members.permissions import can_decide_at_step

    from .board_actions import can_request_approval

    stage = pipeline_stage(post)
    variants = post.platform_posts.all()
    planned = post.scheduled_at
    moves = available_moves(post, membership)
    if PipelineStage.APPROVED not in moves and can_request_approval(post, membership):
        moves.append(PipelineStage.APPROVED)
    return {
        "post": post,
        "stage": stage,
        "moves": [(value, PipelineStage(value).label) for value in moves],
        "can_approve": can_decide(post, membership)
        or (PipelineStage.REVIEW in moves and can_decide_at_step(membership, "pending_review")),
        "can_trash": can_delete_content(membership, post, list(variants))
        and not any(pp.status == "publishing" for pp in variants),
        # Ready needs a date; offered from Approved, and from a draft where approval is not required (P1-B27).
        "can_plan": PipelineStage.READY in moves,
        "client_review": any(pp.status == "pending_client" for pp in variants),
        "date": planned or post.proposed_publish_at,
        "date_label": "Planned" if planned else "Proposed",
        "comment_count": comment_count,
        "channels": _card_channels(variants),
    }


def _card_channels(variants):
    """The board shows the content as a whole (P2-B14): each channel once, with its outputs' formats."""
    channels = {}
    for variant in sorted(variants, key=lambda item: item.output_sort_key):
        account = variant.social_account
        entry = channels.setdefault(
            account.pk,
            {
                "account": account,
                "name": getattr(getattr(account, "jrc_channel", None), "display_name", "") or account.account_name,
                "formats": [],
            },
        )
        entry["formats"].append(variant.format_name)
    return list(channels.values())


def _sort_cards(cards, *, newest_first=False):
    """Order of a board column (P2-B03).

    Dated cards come first, the nearest date at the top; below them the undated ones in the order they
    were created, so the newest content is at the very bottom. Published (``newest_first``) keeps its
    order: the latest publication at the top, undated cards after them by their latest change.
    """
    if newest_first:
        cards.sort(key=lambda card: card["post"].updated_at, reverse=True)
        dated = [card for card in cards if card["date"]]
        dated.sort(key=lambda card: card["date"], reverse=True)
        return dated + [card for card in cards if not card["date"]]
    cards.sort(key=lambda card: (card["post"].created_at, str(card["post"].pk)))
    dated = [card for card in cards if card["date"]]
    dated.sort(key=lambda card: card["date"])
    return dated + [card for card in cards if not card["date"]]


def _board_context(request):
    """The board of the current workspace (P2-B13): one client, no client filter."""
    client = request.GET.get("client", "")
    membership = open_workspace(request, client) if client else current_membership(request)
    by_workspace = {str(membership.workspace_id): membership} if membership else {}
    selected = str(membership.workspace_id) if membership else ""
    channel = request.GET.get("channel", "")
    workspace_ids = list(by_workspace)
    channels = list(
        SocialAccount.objects.filter(workspace_id__in=workspace_ids, jrc_channel__isnull=False)
        .select_related("workspace", "jrc_channel")
        .order_by("workspace__name", "account_name")
    )
    if channel and channel != "none" and channel not in {str(c.pk) for c in channels}:
        raise ValidationError("Choose a channel of this client.")
    # P2-B03: "Content board" on the detail returns to the board as it was last shown; the client
    # in it makes that board current again when the detail belonged to another one.
    board_query = urlencode({"client": selected, "channel": channel})
    if request.session.get(BOARD_QUERY_KEY) != board_query:
        request.session[BOARD_QUERY_KEY] = board_query
    posts = Post.objects.filter(workspace_id__in=workspace_ids)
    if channel == "none":
        posts = posts.filter(platform_posts__isnull=True)
    elif channel:
        # A subquery, not a join: one channel may carry several outputs of a content (P2-B14).
        posts = posts.filter(pk__in=PlatformPost.objects.filter(social_account_id=channel).values("post_id"))
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
    query = request.GET.get("q", "").strip()[:200]
    search_warning = ""
    if query:
        from .search import search_content

        posts, search_warning = search_content(posts, request.user, query)
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
    return {
        "columns": list(columns.values()),
        "waiting_for_you": waiting,
        "workspace": membership.workspace if membership else None,
        "can_create": bool(membership and can_create_content(membership)),
        # The board's own workspace; the move form sends it back so the board reopens on it.
        "selected_client": selected,
        "selected_channel": channel,
        "channels": channels,
        "total": len(posts),
        "search_query": query,
        "search_warning": search_warning,
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
@require_GET
def trash(request):
    """Trash of the current workspace (P2-B09, P2-B13): one client, like the board."""
    membership = current_membership(request)
    cards = []
    if membership is not None:
        posts = Post.all_objects.filter(workspace_id=membership.workspace_id, trashed_at__isnull=False).select_related(
            "workspace"
        )
        for post in posts.order_by("-trashed_at", "pk"):
            copies = list(PlatformPost.all_objects.filter(post=post))
            cards.append({"post": post, "can_manage": can_delete_content(membership, post, copies)})
    return render(
        request,
        "jrc_production/trash.html",
        {
            "cards": cards,
            "can_empty": any(card["can_manage"] for card in cards),
            "workspace": membership.workspace if membership else None,
        },
    )


def creation_options(membership):
    """What may be created in the current workspace (P2-B03, P2-B13): New idea and New content.

    ``membership`` is the one in the current workspace (``current_membership``). New content always
    goes there, so there is never a client to choose: ``fixed`` is that client with its enabled
    channels and the stages new content may start in (``initial_stages``), or None when the user
    may not create content in it. ``clients`` keeps the list form the templates test.
    """
    if membership is None or not can_create_content(membership):
        return {"clients": [], "fixed": None}
    entry = {
        "workspace": membership.workspace,
        "channels": list(
            SocialAccount.objects.filter(workspace_id=membership.workspace_id, jrc_channel__is_enabled=True)
            .select_related("jrc_channel")
            .order_by("account_name", "id")
        ),
        "stages": [(value, PipelineStage(value).label) for value in initial_stages(membership)],
    }
    return {"clients": [entry], "fixed": entry}


def _slot_publish_at(date, time):
    """``datetime-local`` value for a calendar slot's date and time, or "" when either is not valid.

    Calendars send ``YYYY-MM-DD`` and a 24-hour time; the hour may come with or without a leading
    zero (``8:00`` or ``08:00``, as the composer accepted). The value is always normalized.
    """
    try:
        day = datetime.strptime(date, "%Y-%m-%d").date()
        moment = datetime.strptime(time, "%H:%M").time()
    except ValueError:
        return ""
    return f"{day:%Y-%m-%d}T{moment:%H:%M}"


def _publish_at(workspace, value):
    """The submitted ``datetime-local`` value in the client's time zone, or None when empty."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except ValueError:
        raise ValidationError("Choose a valid publication date and time.") from None
    return timezone.make_aware(parsed, ZoneInfo(workspace.effective_timezone or "UTC"))


def _safe_next(request):
    target = request.POST.get("next", "")
    if (
        target.startswith("/")
        and not target.startswith("//")
        and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=False)
    ):
        return target
    return ""


def created_message(post, detail_url):
    """The notice after any content is created, with a link to it (P2-B03, point 5a)."""
    stage = PipelineStage(pipeline_stage(post)).label
    return format_html(
        '“{}” was added to {}. <a href="{}" class="font-semibold underline">Open it</a>',
        post.title,
        stage,
        detail_url,
    )


def _new_content_page(request, *, values=None, error="", status=200):
    options = creation_options(current_membership(request))
    if not options["clients"]:
        raise PermissionDenied("You cannot create content.")
    values = values or {}
    chosen = values.get("channels") or []
    return render(
        request,
        "jrc_production/content_new.html",
        {
            **options,
            "values": values,
            "chosen_channels": [str(value) for value in chosen],
            "error": error,
            "board_url": _board_url(request),
        },
        status=status,
    )


@login_required
@require_GET
def new_content(request):
    """An empty content detail to fill in (P2-B03, point 1b); the old composer's "new post" lands here."""
    date, time = request.GET.get("date", ""), request.GET.get("time", "") or "09:00"
    publish_at = _slot_publish_at(date, time) if date else ""
    stage = request.GET.get("stage", "")
    account = request.GET.get("account", "")
    values = {
        "publish_at": publish_at,
        "stage": stage if stage in PipelineStage.values else "",
        "channels": [account] if _UUID_RE.match(account) else [],
    }
    if request.GET.get("client"):
        # Links from a client's calendar name it; the page then opens in that client (P2-B13).
        open_workspace(request, request.GET["client"])
    return _new_content_page(request, values=values)


@login_required
@require_POST
def create(request):
    """Create content from the New idea window or the New content page (P2-B03)."""
    # Resolve against the current user's memberships before parsing a submitted UUID.
    memberships = {str(m.workspace_id): m for m in _memberships(request.user)}
    membership = memberships.get(request.POST.get("workspace", ""))
    if membership is None:
        raise Http404
    workspace = membership.workspace
    from_page = request.POST.get("origin") == "page"
    in_place = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    refused = _refuse_other_workspace(request, membership)
    if refused:
        return _refusal_response(
            request, refused, reverse("jrc_production:new_content") if from_page else reverse("jrc_production:board")
        )
    values = {
        "title": request.POST.get("title", ""),
        "description": request.POST.get("description", ""),
        "stage": request.POST.get("stage", "") or ProductionStage.IDEA,
        "publish_at": request.POST.get("publish_at", ""),
        "channels": request.POST.getlist("channels"),
    }
    try:
        post = create_content(
            workspace,
            values["title"],
            request.user,
            social_account_ids=values["channels"],
            description=values["description"],
            stage=values["stage"],
            publish_at=_publish_at(workspace, values["publish_at"]),
        )
    except ValidationError as exc:
        error = " ".join(exc.messages)
        if from_page:
            return _new_content_page(request, values=values, error=error, status=400)
        if in_place:
            return HttpResponseBadRequest(error, content_type="text/plain; charset=utf-8")
        messages.error(request, error)
        return redirect(_safe_next(request) or reverse("jrc_production:board") + f"?client={workspace.id}")
    detail_url = reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.id, "post_id": post.id})
    if from_page:
        # The page opens the new content itself; the notice says where it went.
        messages.success(request, f"Content created in {PipelineStage(pipeline_stage(post)).label}.")
        return redirect(detail_url)
    messages.success(request, created_message(post, detail_url))
    target = _safe_next(request) or reverse("jrc_production:board") + f"?client={workspace.id}"
    if in_place:
        return JsonResponse({"redirect": target, "url": detail_url}, status=201)
    return redirect(target)


@login_required
@require_POST
def quick_idea(request):
    """Save a one-line idea without channel or date; the board stays open for the next one."""
    memberships = {str(m.workspace_id): m for m in _memberships(request.user)}
    membership = memberships.get(request.POST.get("workspace", ""))
    if membership is None:
        raise Http404
    in_place = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    refused = _refuse_other_workspace(request, membership)
    if refused:
        return _refusal_response(request, refused, reverse("jrc_production:board"))
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
    if not in_place:
        detail_url = reverse(
            "jrc_production:content_detail", kwargs={"workspace_id": membership.workspace_id, "post_id": post.id}
        )
        messages.success(request, created_message(post, detail_url))
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
    response["X-Content-Url"] = reverse(
        "jrc_production:content_detail", kwargs={"workspace_id": membership.workspace_id, "post_id": post.id}
    )
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
        from .board_actions import board_move

        moved, notice = board_move(
            post,
            request.POST.get("stage", ""),
            request.user,
            expected_stage=request.POST.get("expected_stage", ""),
            scheduled_at=scheduled_at,
            comment=request.POST.get("comment", ""),
        )
    except (ValidationError, ValueError) as exc:
        error = " ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
        messages.error(request, error)
    else:
        messages.success(request, notice)
    # Preserve only validated board filter values, never redirect to caller-supplied URLs.
    filters = {key: request.POST.get(key, "") for key in ("client", "channel")}
    return redirect(reverse("jrc_production:board") + "?" + urlencode(filters))


@login_required
@require_POST
def rename(request, workspace_id, post_id):
    """Rename content from its detail page. The one rule is ``services.rename_content``."""
    workspace = get_object_or_404(Workspace, pk=workspace_id)
    if not WorkspaceMembership.objects.filter(user=request.user, workspace=workspace).exists():
        raise Http404
    post = get_object_or_404(Post, pk=post_id, workspace=workspace)
    ajax = request.headers.get("Accept") == "application/json"
    try:
        reverted = rename_content(post, request.POST.get("title", ""), request.user)
    except ValidationError as exc:
        error = " ".join(exc.messages)
        if ajax:
            return JsonResponse({"error": error}, status=400)
        messages.error(request, error)
    else:
        notice = "The title is unchanged." if reverted is None else "Content renamed."
        if reverted:
            notice += " " + review_return_notice(reverted, "title")
        if ajax:
            post.refresh_from_db(fields=["title"])
            return JsonResponse({"title": post.title, "message": notice})
        messages.success(request, notice)
    return redirect(reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.pk, "post_id": post.pk}))


@login_required
@require_POST
def category(request, workspace_id, post_id):
    post = get_object_or_404(Post, pk=post_id, workspace_id=workspace_id)
    content_membership(post.workspace, request.user)
    try:
        selected = change_category(post, request.POST.get("category", ""), request.user)
    except ValidationError as exc:
        return JsonResponse({"error": " ".join(exc.messages)}, status=400)
    return JsonResponse({"category": str(selected.pk) if selected else ""})


@login_required
@require_POST
def proposed_date(request, workspace_id, post_id):
    post = get_object_or_404(Post, pk=post_id, workspace_id=workspace_id)
    membership = content_membership(post.workspace, request.user)
    if not can_rename_content(post, membership):
        raise PermissionDenied("You cannot edit this content.")
    form = ProposedDateForm(request.POST)
    with timezone.override(ZoneInfo(post.workspace.effective_timezone)):
        if not form.is_valid():
            return JsonResponse({"error": " ".join(form.errors["proposed_publish_at"])}, status=400)
        value = form.cleaned_data["proposed_publish_at"]
        try:
            change_proposed_date(post, value, request.user)
        except ValidationError as exc:
            return JsonResponse({"error": " ".join(exc.messages)}, status=400)
        return JsonResponse(
            {"proposed_publish_at": timezone.localtime(value).strftime("%Y-%m-%dT%H:%M") if value else ""}
        )


# P1-B08: Concept (the idea worked out in versions) comes before the Script.
TABS = ("concept", "script", "shotlist", "video", "caption", "approval", "discussion", "publish", "history")


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


def enabled_outputs(post):
    """The content's outputs (channel + format) on enabled channels, by channel name and format (P2-B14).

    The one list behind the Video, Caption, Publish and Approval tabs: each output is its own channel
    copy with its own video, caption, thumbnail and date. Switched-off channels keep their stored work
    but are not listed; a channel is never attached implicitly.
    """
    variants = [
        variant
        for variant in post.platform_posts.select_related("social_account__jrc_channel")
        if variant.social_account.workspace_id == post.workspace_id
        and getattr(getattr(variant.social_account, "jrc_channel", None), "is_enabled", True)
    ]
    return sorted(variants, key=lambda variant: variant.output_sort_key)


def _caption_channels(post, submitted=None):
    channels = []
    for variant in enabled_outputs(post):
        account = variant.social_account
        key = str(variant.pk)
        document = (
            TextDocument.objects.filter(platform_post=variant, kind=TextDocument.Kind.CAPTION).first()
            if variant
            else None
        )
        latest = (
            document.versions.filter(state=TextVersion.State.FINAL)
            .select_related("created_by", "label")
            .order_by("-number")
            .first()
            if document
            else None
        )
        previous = (
            document.versions.filter(state=TextVersion.State.FINAL, number__lt=latest.number)
            .select_related("created_by", "label")
            .order_by("-number")
            .first()
            if latest
            else None
        )
        saved_text = latest.text if latest else (variant.effective_caption if variant else (post.caption or ""))
        submitted_caption = (submitted or {}).get("caption") if (submitted or {}).get("platform_post") == key else None
        current_text = submitted_caption if submitted_caption is not None else saved_text
        old_diff, new_diff = _word_diff(previous.text, current_text) if previous else ([], [])
        channels.append(
            {
                "key": key,
                "variant": variant,
                "account": account,
                "platform_label": account.get_platform_display(),
                "label": variant.output_title,
                "document": document,
                "latest": latest,
                "previous": previous,
                "text": current_text,
                "char_count": account.caption_wire_length(current_text),
                "char_limit": account.char_limit,
                "escaped_chars": account.escaped_chars,
                "old_diff": old_diff,
                "new_diff": new_diff,
                "versions": list(
                    document.versions.filter(state="final").select_related("created_by", "label").order_by("-number")
                )
                if document
                else [],
                "reopens_review": review_return_hint(variant) if variant else "",
            }
        )
    return channels


def _create_missing_copy(post, account_id):
    """Legacy caption/video forms may reference a copy, but never attach a new channel implicitly.

    ``new:<account>`` keys predate outputs (P2-B14); they resolve to the channel's first output.
    """
    from apps.composer.output_formats import format_sort_key

    variant = min(
        post.platform_posts.filter(social_account_id=account_id),
        key=lambda item: format_sort_key(item.output_format),
        default=None,
    )
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
    if active_tab == "comments":  # Existing links and notifications remain valid.
        active_tab = "discussion"
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
                account_id, output_format = parse_output_choice(request.POST.get("channel"))
                account = get_object_or_404(SocialAccount, pk=account_id, workspace=workspace)
                add_content_channel(post, account.pk, request.user, output_format)
            else:
                variant = get_object_or_404(PlatformPost, pk=request.POST.get("platform_post"), post=post)
                remove_content_channel(post, variant.pk, request.user)
        except ValidationError as exc:
            channel_error = " ".join(exc.messages)
        except PermissionDenied as exc:
            # P1-B37: the deletion rule explains itself (planned content needs the planning right).
            channel_error = str(exc) or "You cannot change channels for this content."
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
            try:
                variant = get_object_or_404(
                    PlatformPost.objects.select_related("post", "social_account"),
                    pk=target,
                    post=post,
                )
            except ValidationError:
                # Not a UUID at all: the same answer as an unknown copy, not an error page (P2-FABLE).
                raise Http404 from None
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
                    result = text_services.save_caption(
                        post.pk, variant.pk, request.user, caption, base_version_id=base_id or None
                    )
            except ConflictError as exc:
                caption_error = str(exc) or "This caption changed in another tab. Reload the page."
            except ValidationError as exc:
                caption_error = " ".join(exc.messages)
            else:
                notice = review_return_notice(result.returned, "caption")
                messages.success(request, f"Caption saved as a new version. {notice}".strip())
                return redirect(f"{request.path}?tab=caption")

    caption_channels = _caption_channels(post, request.POST if request.method == "POST" else None)
    can_view_internal_notes = membership.workspace_role not in ("client", "viewer")
    comments = [
        c
        for c in comment_services.get_comments_for_post(post, request.user)
        if comment_services.section_key(c)[0] == "discussion"
    ]
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
            "label": f"Marked {publication.platform_post.output_title} as published",
            "actor": publication.published_by,
            "note": publication.post_url,
            "can_view_note": True,
        }
        for publication in ManualPublication.objects.filter(platform_post__post=post).select_related(
            "published_by", "platform_post__social_account__jrc_channel"
        )
    )
    history.extend(
        {
            "kind": "rename",
            "created_at": change.created_at,
            "label": (
                f"Renamed from “{change.old_title}” to “{change.new_title}”"
                if change.old_title and change.new_title
                else f"Named “{change.new_title}”"
                if change.new_title
                else f"Title “{change.old_title}” removed"
            ),
            "actor": change.user,
            "note": "",
            "can_view_note": True,
        }
        for change in ContentRename.objects.filter(post=post).select_related("user")
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
            "label": (
                # P2-B15: the same file used for another output names both outputs.
                f"Used video {version.copied_from_label} for {version.video.platform_post.output_title}"
                f" as v{version.number}"
                + (" · comments kept" if version.comments_copied is not None else " · without comments")
                if version.copied_from_label
                else f"Uploaded video v{version.number} · {version.video.platform_post.output_title}"
            ),
            "actor": version.uploaded_by,
            "note": "",
            "can_view_note": True,
        }
        for version in VideoVersion.objects.filter(video__platform_post__post=post)
        .exclude(status=VideoVersion.Status.UPLOADING)
        .select_related("uploaded_by", "video__platform_post__social_account__jrc_channel")
    )
    history.extend(
        {
            "kind": "video",
            "created_at": event.created_at,
            "label": f"{event.get_action_display()} · v{event.version.number} · {event.version.video.platform_post.output_title}",
            "actor": event.actor,
            "note": "",
            "can_view_note": True,
        }
        for event in VideoActivity.objects.filter(version__video__platform_post__post=post).select_related(
            "actor", "version__video__platform_post__social_account__jrc_channel"
        )
    )
    history.extend(
        {
            "kind": "text",
            "created_at": version.finalized_at,
            "label": (
                f"Saved {version.document.get_kind_display()} version {version.number}"
                + (f" · {version.document.platform_post.output_title}" if version.document.platform_post_id else "")
                + (" · Empty text" if not version.text.strip() else "")
            ),
            "actor": version.created_by,
            "note": "",
            "can_view_note": True,
        }
        for version in TextVersion.objects.filter(document__post=post, state=TextVersion.State.FINAL)
        .defer("content")
        .select_related("created_by", "document__platform_post__social_account__jrc_channel")
    )
    history.extend(
        {
            "kind": "text",
            "created_at": event.created_at,
            "actor": event.user,
            "label": f"{event.version.document.get_kind_display()} V{event.version.number} name changed from “{event.old_name or 'None'}” to “{event.new_name or 'None'}”",
            "note": "",
            "can_view_note": True,
        }
        for event in TextVersionNameChange.objects.filter(version__document__post=post).select_related(
            "user", "version__document"
        )
    )
    history.extend(
        {
            "kind": "category",
            "created_at": event.created_at,
            "actor": event.user,
            "label": f"Category changed from “{event.old_name or 'None'}” to “{event.new_name or 'None'}”",
            "note": "",
            "can_view_note": True,
        }
        for event in ContentCategoryChange.objects.filter(post=post).select_related("user")
    )

    def proposal_label(value):
        if value is None:
            return "None"
        return timezone.localtime(value, ZoneInfo(workspace.effective_timezone)).strftime("%b %d, %Y · %H:%M %Z")

    history.extend(
        {
            "kind": "proposal",
            "created_at": event.created_at,
            "actor": event.user,
            "label": f"Proposed publication changed from “{proposal_label(event.old_at)}” to “{proposal_label(event.new_at)}”",
            "note": "",
            "can_view_note": True,
        }
        for event in ContentProposalChange.objects.filter(post=post).select_related("user")
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
            enabled_outputs(post),
            can_view_internal_notes=can_view_internal_notes,
        )
    script_sections = []
    if active_tab == "script":
        # P2-B14: one script with a section per format of the content (shown when it has several).
        from apps.composer.output_formats import format_sort_key

        script_sections = [
            OutputFormat(value).label
            for value in sorted(set(post.platform_posts.values_list("output_format", flat=True)), key=format_sort_key)
            if value in OutputFormat.values
        ]
    shotlist_ctx = {}
    if active_tab == "shotlist":
        shotlist_ctx = _shotlist_context(
            post, membership, focus=request.GET.get("focus", ""), output_format=request.GET.get("format", "")
        )
    publish_channels = []
    can_publish = can_publish_directly(membership)
    if active_tab == "publish":
        from apps.jrc_video.models import VideoAsset, VideoVersion

        videos = {
            item.platform_post_id: item
            for item in VideoAsset.objects.filter(platform_post__post=post).select_related("final_version")
        }
        publications = {
            item.platform_post_id: item for item in ManualPublication.objects.filter(platform_post__post=post)
        }
        for variant in enabled_outputs(post):
            account = variant.social_account
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
                    "label": variant.output_title,
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
    # P2-B08: switching tabs asks only for the panel (content-tabs.js); a direct visit gets the whole page.
    fragment = request.method == "GET" and request.headers.get("X-JRC-Tab") == "1"
    response = render(
        request,
        "jrc_production/_detail_panel.html" if fragment else "jrc_production/content_detail.html",
        {
            **video_context,
            **approval_ctx,
            **shotlist_ctx,
            "workspace": workspace,
            "post": post,
            "pipeline_stage": pipeline_stage(post),
            "active_tab": active_tab,
            "text_colors": TEXT_COLORS,
            "script_sections": script_sections,
            "tabs": TABS,
            "history": history[:100],
            "empty_initial_text": TextVersion.objects.filter(
                document__post=post, document__kind=active_tab, state=TextVersion.State.FINAL, number=1, text=""
            ).exists()
            if active_tab in {"concept", "script"}
            else False,
            "can_view_internal_notes": can_view_internal_notes,
            "can_edit_posts": text_services.can_edit_text(post, membership),
            "can_edit_proposal": can_rename_content(post, membership) and can_change_proposal(post),
            "comments": comments,
            "can_resolve_comments": can_moderate_comments(membership),
            "can_comment": can_comment(membership),
            "comment_visibility_default": (
                PostComment.Visibility.EXTERNAL if is_client else PostComment.Visibility.INTERNAL
            ),
            "is_client": is_client,
            "can_edit_captions": can_edit_captions,
            "content_categories": workspace.content_categories.all(),
            "can_rename": can_rename_content(post, membership),
            "channel_error": channel_error,
            "needs_channel": not post.platform_posts.exists() and active_tab in {"video", "caption", "publish"},
            "caption_channels": caption_channels,
            "caption_error": caption_error,
            "publish_channels": publish_channels,
            "can_publish": can_publish,
            "board_url": _board_url(request),
            # P2-B03: Delete moved here from the old composer; the one deletion rule decides (P1-B37).
            "can_delete": can_delete_content(membership, post, list(post.platform_posts.all())),
            **_channel_tags_context(post, membership),
        },
    )
    # The panel and the page share one address: a cache must never answer one with the other.
    patch_vary_headers(response, ["X-JRC-Tab"])
    if fragment:
        add_never_cache_headers(response)
        response["X-JRC-Tab"] = active_tab
    return response


def _channel_tags_context(post, membership):
    """The channel tags in the detail header (P2-B08); the same rules as the services that change them."""
    return {
        "can_manage_channels": can_manage_channels(post, membership),
        # ``_lock_channel_content``: channels change only while every copy is still in Editing.
        "channels_locked": post.platform_posts.exclude(status=PlatformPost.Status.DRAFT).exists(),
        # P2-B14: what "+" offers is an output (channel + format) the content does not have yet.
        "addable_outputs": addable_outputs(post),
        "content_outputs": sorted(post.platform_posts.all(), key=lambda variant: variant.output_sort_key),
    }


def _json_body(request):
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise ValidationError("Send a valid JSON object.") from exc
    if not isinstance(data, dict):
        raise ValidationError("Send a valid JSON object.")
    return data


@login_required
@require_POST
def channels(request, workspace_id, post_id):
    """Add or remove a channel of the content from its tags, without a page load (P2-B08).

    The same services and rules as the detail form (``add_content_channel``/``remove_content_channel``);
    answers with the re-rendered tags so the page shows exactly what is stored.
    """
    workspace = get_object_or_404(Workspace, pk=workspace_id)
    membership = WorkspaceMembership.objects.filter(user=request.user, workspace=workspace).first()
    if membership is None:
        raise Http404
    post = get_object_or_404(Post.objects.filter(workspace=workspace), pk=post_id)
    if not can_manage_channels(post, membership):
        return JsonResponse({"error": "You cannot change channels for this content."}, status=403)
    action = request.POST.get("action")
    status, message = 200, ""
    try:
        if action == "add_channel":
            account_id, output_format = parse_output_choice(request.POST.get("channel"))
            account = SocialAccount.objects.filter(pk=account_id, workspace=workspace).first()
            if account is None:
                raise ValidationError("Choose an enabled channel of this client.")
            add_content_channel(post, account.pk, request.user, output_format)
            message = "Output added."
        elif action == "remove_channel":
            variant = PlatformPost.objects.filter(pk=request.POST.get("platform_post"), post=post).first()
            if variant is None:
                raise ValidationError("This channel is no longer part of the content. Reload the page.")
            remove_content_channel(post, variant.pk, request.user)
            message = "Output removed."
        else:
            return HttpResponseBadRequest("Unknown action.")
    except ValidationError as exc:
        status, message = 400, " ".join(exc.messages)
    except PermissionDenied as exc:
        status, message = 403, str(exc) or "You cannot change channels for this content."
    post = Post.objects.prefetch_related("platform_posts__social_account__jrc_channel").get(pk=post.pk)
    html = render_to_string(
        "jrc_production/_channel_tags.html",
        {
            "post": post,
            "workspace": workspace,
            "active_tab": request.POST.get("tab", ""),
            **_channel_tags_context(post, membership),
        },
        request=request,
    )
    key = "message" if status == 200 else "error"
    return JsonResponse({"html": html, key: message}, status=status)


@login_required
@require_POST
def caption_autosave(request, workspace_id, post_id, platform_post_id):
    """Automatic saving of one channel caption (P2-B08); the version rule is in ``autosave_caption``."""
    workspace = get_object_or_404(Workspace, pk=workspace_id)
    membership = WorkspaceMembership.objects.filter(user=request.user, workspace=workspace).first()
    if membership is None:
        raise Http404
    post = get_object_or_404(Post.objects.filter(workspace=workspace), pk=post_id)
    variant = get_object_or_404(PlatformPost, pk=platform_post_id, post=post)
    if not text_services.can_edit_text(post, membership):
        return JsonResponse({"error": "You cannot edit captions for this content."}, status=403)
    try:
        data = _json_body(request)
        base = data.get("base") or None
        if (
            base is not None
            and not TextVersion.objects.filter(
                pk=base, document__platform_post=variant, document__kind=TextDocument.Kind.CAPTION, state="final"
            ).exists()
        ):
            raise ConflictError("This caption changed in another place. Reload the text.")
        result = text_services.autosave_caption(
            post.pk,
            variant.pk,
            request.user,
            data.get("text"),
            base_version_id=base,
            revision=data.get("revision"),
            device=data.get("device"),
        )
    except ConflictError as exc:
        return JsonResponse({"error": str(exc)}, status=409)
    except ValidationError as exc:
        return JsonResponse({"error": " ".join(exc.messages)}, status=400)
    except (TypeError, ValueError):
        return JsonResponse({"error": "Send a valid JSON object."}, status=400)
    post = Post.objects.select_related("workspace").get(pk=post.pk)
    item = next(
        (item for item in _caption_channels(post) if item["variant"] and item["variant"].pk == variant.pk), None
    )
    version, concurrent = result.version, result.concurrent
    return JsonResponse(
        {
            "version": {
                "id": str(version.pk),
                "number": version.number,
                "revision": version.revision,
                "label": " · ".join(
                    (
                        f"V{version.number}" + (f" – {version.name}" if version.name else ""),
                        version.created_by.display_name if version.created_by else "Former member",
                        date_format(timezone.localtime(version.finalized_at), "M j, Y · g:i A"),
                    )
                ),
            }
            if version
            else None,
            "created": result.created,
            "changed": result.changed,
            "concurrent": {
                "number": concurrent.number,
                "author": concurrent.created_by.display_name if concurrent.created_by else "Former member",
            }
            if concurrent
            else None,
            "notice": review_return_notice(result.returned, "caption"),
            "hint": item["reopens_review"] if item else "",
            "versions_html": render_to_string(
                "jrc_production/_caption_versions.html",
                {"item": item, "workspace": workspace, "post": post, "can_edit_captions": True},
                request=request,
            )
            if item
            else "",
        }
    )


_SHOTLIST_HISTORY_LABELS = {
    ShotlistChange.Action.ADDED: 'Added "{text}" to the shotlist',
    ShotlistChange.Action.DONE: 'Checked off "{text}" in the shotlist',
    ShotlistChange.Action.UNDONE: 'Unchecked "{text}" in the shotlist',
    ShotlistChange.Action.DELETED: 'Removed "{text}" from the shotlist',
}


def _shotlist_context(post, membership, *, focus="", error="", new_text="", new_note="", output_format=""):
    # P2-B14: one list per format of the content; an unknown format opens the first list.
    formats = shotlist_services.shotlist_formats(post)
    try:
        current = shotlist_services.clean_format(post, output_format, formats)
    except ValidationError:
        current = formats[0] if formats else ""
    items = shotlist_services.items_for(post, current, formats=formats)
    done, total = shotlist_services.progress(items)
    content = [{"id": str(item.pk), "text": item.text, "note": item.note, "done": item.is_done} for item in items]
    return {
        "shotlist_formats": [
            {"value": value, "label": OutputFormat(value).label if value in OutputFormat.values else value}
            for value in formats
        ],
        "shotlist_format": current,
        "shotlist_comment_version": shotlist_services.current_version(post, current, content=content),
        "shotlist_comment_state": shotlist_services.state_token(post, current, content),
        "shotlist_format_label": OutputFormat(current).label if current in OutputFormat.values else "",
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
    output_format = request.POST.get("output_format", "")
    focus, error, status = "", "", 200
    if action == "update" and request.headers.get("X-JRC-Shotlist-Autosave") == "1":
        # P2-B08: editing right in the text saves automatically and answers with JSON, so the list is
        # not redrawn under the person typing.
        try:
            item = shotlist_services.update_item(post, item_id, request.user, text=text, note=note)
        except shotlist_services.ItemNotFoundError as exc:
            return JsonResponse({"error": str(exc)}, status=409)
        except ValidationError as exc:
            return JsonResponse({"error": " ".join(exc.messages)}, status=400)
        except PermissionDenied as exc:
            return JsonResponse({"error": str(exc) or "You cannot edit this shotlist."}, status=403)
        who = item.updated_by.display_name or item.updated_by.email if item.updated_by else ""
        version = shotlist_services.current_version(post, item.output_format)
        return JsonResponse(
            {
                "text": item.text,
                "note": item.note,
                "comment_version": str(version.pk) if version else "",
                "comment_state": shotlist_services.state_token(post, item.output_format),
                "meta": f"{'Checked off' if item.is_done else 'Updated'} by {who} · "
                f"{date_format(timezone.localtime(item.updated_at), 'M j, g:i A')}"
                if who
                else "",
            }
        )
    try:
        if action == "add":
            shotlist_services.add_item(post, request.user, text or "", note or "", output_format=output_format)
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
            output_format=output_format,
        )
        return render(
            request, "jrc_production/_shotlist.html", {**context, "workspace": workspace, "post": post}, status=status
        )
    if error:
        messages.error(request, error)
    url = reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.id, "post_id": post.id})
    format_query = f"&format={output_format}" if output_format in OutputFormat.values else ""
    if focus == "add" and not error:
        # No fragment here: browsers skip autofocus when the URL targets an element.
        return redirect(f"{url}?tab=shotlist{format_query}&focus=add")
    anchor = f"#shotlist-{focus}" if focus and not error else "#shotlist"
    return redirect(f"{url}?tab=shotlist{format_query}{anchor}")


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
        # The same answer RBACMiddleware gives a stranger on every jrc_* URL (P2-FABLE).
        raise Http404
    post = get_object_or_404(Post, pk=post_id, workspace=workspace)
    return workspace, post


def _comments_redirect(workspace, post):
    return redirect(
        reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.id, "post_id": post.id})
        + "?tab=discussion"
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
