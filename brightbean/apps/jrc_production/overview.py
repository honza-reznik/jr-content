# JR Content: přidáno 2026-11-14 (blok P2-B13), AGPL-3.0.
"""Admin overview (P2-B13, point 4h): every client of the organization in one board and calendar.

Only the owner and the admins of the organization see it, also for workspaces they are not a
member of. It only reads: no moves, no creating, no comments. A card opens a read-only summary of
the content here; members of its workspace get a link to the regular detail as well.

What the summary shows follows the rule of the regular detail (``can_see_internal_notes``): whoever is a
member of the workspace sees what their workspace role sees, so a client or viewer never gets the
"Team only" comments. Without membership the organization owner sees everything (an owner may add
themselves to any workspace), while an organization admin sees only what a client sees, because
an admin has no such right and would otherwise read more here than in the detail.
Working on content still needs the membership, because every write checks the workspace role.
"""

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Prefetch, Q
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from apps.approvals.models import PostComment
from apps.composer.models import PlatformPost, Post
from apps.jrc_texts.models import TextDocument, TextVersion
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.workspaces.models import Workspace

from .models import PipelineStage
from .services import pipeline_stage

OVERVIEW_ROLES = (OrgMembership.OrgRole.OWNER, OrgMembership.OrgRole.ADMIN)


def can_view_overview(org_membership):
    """The one rule for the overview and its menu item: owner or admin of the organization."""
    return bool(
        org_membership is not None
        and org_membership.org_role in OVERVIEW_ROLES
        and org_membership.organization.deleted_at is None
    )


def overview_organization(request):
    """The organization the overview shows, or 404 for anyone who may not see it."""
    membership = getattr(request, "org_membership", None)
    if not request.user.is_active or not can_view_overview(membership):
        raise Http404
    return membership.organization


def overview_workspaces(organization):
    """All active clients of the organization, membership or not."""
    return Workspace.objects.filter(organization=organization, is_archived=False).order_by("name", "id")


def can_see_internal_notes(user, organization_membership, workspace_membership):
    """May this person read "Team only" comments of a workspace in the overview?

    A workspace member follows the workspace role (client and viewer do not). A non-member
    follows the organization role: the owner sees all, an admin only what a client sees.
    """
    if workspace_membership is not None:
        return workspace_membership.workspace_role not in (
            WorkspaceMembership.WorkspaceRole.CLIENT,
            WorkspaceMembership.WorkspaceRole.VIEWER,
        )
    return bool(organization_membership is not None and organization_membership.org_role == OrgMembership.OrgRole.OWNER)


def _member_workspace_ids(user, workspace_ids):
    return set(
        WorkspaceMembership.objects.filter(user=user, workspace_id__in=workspace_ids).values_list(
            "workspace_id", flat=True
        )
    )


@login_required
@require_GET
def overview_board(request):
    """All content of the organization by stage, with a client filter (read only)."""
    from .views import _card_channels, _sort_cards

    organization = overview_organization(request)
    workspaces = list(overview_workspaces(organization))
    selected = request.GET.get("client", "")
    shown = workspaces
    if selected:
        shown = [workspace for workspace in workspaces if str(workspace.id) == selected]
        if not shown:
            return HttpResponseBadRequest("Choose a client of this organization.")
    posts = list(
        Post.objects.filter(workspace__in=shown)
        .select_related("workspace", "jrc_production")
        .annotate(
            shotlist_total=Count("jrc_shotlist_items", distinct=True),
            shotlist_done=Count("jrc_shotlist_items", filter=Q(jrc_shotlist_items__is_done=True), distinct=True),
        )
        .prefetch_related(
            Prefetch("platform_posts", queryset=PlatformPost.objects.select_related("social_account__jrc_channel"))
        )
        .order_by("-updated_at", "id")
    )
    columns = {value: {"value": value, "label": label, "cards": []} for value, label in PipelineStage.choices}
    for post in posts:
        stage = pipeline_stage(post)
        planned = post.scheduled_at
        columns[stage]["cards"].append(
            {
                "post": post,
                "stage": stage,
                "date": planned or post.proposed_publish_at,
                "date_label": "Planned" if planned else "Proposed",
                "client_review": any(pp.status == "pending_client" for pp in post.platform_posts.all()),
                # P2-B14 outputs: each channel once with its outputs' formats, as on the board.
                "channels": _card_channels(post.platform_posts.all()),
            }
        )
    for column in columns.values():
        column["cards"] = _sort_cards(column["cards"], newest_first=column["value"] == PipelineStage.PUBLISHED)
    return render(
        request,
        "jrc_production/overview_board.html",
        {
            "organization": organization,
            "workspaces": workspaces,
            "selected_client": selected,
            "columns": list(columns.values()),
            "total": len(posts),
            "overview_tab": "board",
        },
    )


def _latest_text(post, kind, platform_post=None):
    document = TextDocument.objects.filter(post=post, kind=kind, platform_post=platform_post).first()
    if document is None:
        return None
    return (
        document.versions.filter(state=TextVersion.State.FINAL)
        .select_related("created_by")
        .defer("content")
        .order_by("-number")
        .first()
    )


@login_required
@require_GET
def overview_content(request, post_id):
    """A read-only summary of one content for the overview (any client of the organization)."""
    from apps.composer.output_formats import OutputFormat
    from apps.jrc_video.models import VideoVersion

    from . import shotlist as shotlist_services

    organization = overview_organization(request)
    post = get_object_or_404(
        Post.objects.select_related("workspace", "author", "category", "jrc_production"),
        pk=post_id,
        workspace__organization=organization,
        workspace__is_archived=False,
    )
    workspace = post.workspace
    variants = sorted(
        post.platform_posts.select_related("social_account__jrc_channel"), key=lambda item: item.output_sort_key
    )
    captions = []
    for variant in variants:
        latest = _latest_text(post, TextDocument.Kind.CAPTION, variant)
        captions.append(
            {"variant": variant, "latest": latest, "text": latest.text if latest else variant.effective_caption}
        )
    membership = WorkspaceMembership.objects.filter(user=request.user, workspace=workspace).first()
    comment_qs = PostComment.objects.filter(post=post, deleted_at__isnull=True)
    if not can_see_internal_notes(request.user, request.org_membership, membership):
        comment_qs = comment_qs.filter(visibility=PostComment.Visibility.EXTERNAL)
    # Comments on a text version (P2-B16) are listed too, labelled with their section and version.
    comments = list(
        comment_qs.select_related("author", "text_version__document", "shotlist_version").order_by("created_at")
    )
    replies = {}
    for comment in comments:
        if comment.parent_comment_id:
            replies.setdefault(comment.parent_comment_id, []).append(comment)
    threads = [{"comment": c, "replies": replies.get(c.pk, [])} for c in comments if not c.parent_comment_id]
    is_member = membership is not None
    # P2-B14: one shotlist per format of the outputs (one list when the content has no output).
    formats = shotlist_services.shotlist_formats(post)
    shotlists = [
        {
            "label": OutputFormat(value).label if value in OutputFormat.values else value,
            "items": shotlist_services.items_for(post, value, formats=formats),
        }
        for value in formats or [""]
    ]
    return render(
        request,
        "jrc_production/overview_content.html",
        {
            "organization": organization,
            "post": post,
            "workspace": workspace,
            "stage_label": PipelineStage(pipeline_stage(post)).label,
            "variants": variants,
            "concept": _latest_text(post, TextDocument.Kind.CONCEPT),
            "script": _latest_text(post, TextDocument.Kind.SCRIPT),
            "captions": captions,
            "shotlists": shotlists,
            "videos": list(
                VideoVersion.objects.filter(video__platform_post__post=post)
                .exclude(status=VideoVersion.Status.UPLOADING)
                .select_related("video__platform_post__social_account__jrc_channel")
                .order_by(
                    "video__platform_post__social_account__account_name",
                    "video__platform_post__output_format",
                    "-number",
                )
            ),
            "threads": threads,
            "stage_changes": list(post.jrc_stage_changes.select_related("user").order_by("-created_at")[:20]),
            "is_member": is_member,
            "overview_tab": "board",
        },
    )
