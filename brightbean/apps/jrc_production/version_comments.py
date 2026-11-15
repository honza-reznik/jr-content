"""Version comment panels: reuse the Discussion service and enforce document boundaries."""

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.approvals import comments as service
from apps.approvals.models import PostComment
from apps.composer.models import Post
from apps.jrc_texts.models import TextDocument
from apps.members.permissions import can_comment, can_moderate_comments

from . import shotlist
from .services import content_membership


def panel_context(post, user, kind, *, platform_post="", output_format=""):
    membership = content_membership(post.workspace, user)
    version = None
    if kind == "shotlist":
        output_format = shotlist.clean_format(post, output_format)
        version = shotlist.current_version(post, output_format)
        key = (kind, output_format)
    elif kind in ("concept", "script", "caption"):
        documents = TextDocument.objects.filter(post=post, kind=kind)
        if kind == "caption":
            # Validate the output even when it does not have a caption document yet.
            copy = get_object_or_404(post.platform_posts, pk=platform_post)
            documents = documents.filter(platform_post=copy)
        document = documents.first()
        version = document.versions.filter(state="final").order_by("-number").first() if document else None
        key = (kind, str(document.pk)) if document else (kind, None)
    else:
        raise Http404("Section not found.")
    comments = [c for c in service.get_comments_for_post(post, user) if service.section_key(c) == key]
    return {
        "post": post,
        "workspace": post.workspace,
        "comments": comments,
        "comment_kind": kind,
        "comment_output": platform_post,
        "comment_format": output_format,
        "comment_version": version,
        "comment_state": shotlist.state_token(post, output_format) if kind == "shotlist" else "",
        "can_comment": can_comment(membership),
        "can_resolve_comments": can_moderate_comments(membership),
        "is_client": membership.workspace_role in ("client", "viewer"),
    }


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def panel(request, workspace_id, post_id):
    post = get_object_or_404(
        Post.objects.select_related("workspace"), pk=post_id, workspace_id=workspace_id, trashed_at__isnull=True
    )
    content_membership(post.workspace, request.user)
    params = request.POST if request.method == "POST" else request.GET
    try:
        ctx = panel_context(
            post,
            request.user,
            params.get("kind"),
            platform_post=params.get("output", ""),
            output_format=params.get("format", ""),
        )
        if request.method == "POST":
            action = params.get("action", "add")
            root = None
            if params.get("parent_id") or params.get("comment_id"):
                root = next(
                    (c for c in ctx["comments"] if str(c.pk) == (params.get("parent_id") or params.get("comment_id"))),
                    None,
                )
                if root is None:
                    raise Http404("Comment thread not found.")
            if action == "resolve" and root:
                service.resolve_comment(
                    root.pk, request.user, post.workspace, resolved=params.get("resolved") == "true"
                )
            elif action == "add":
                version = ctx["comment_version"]
                is_shotlist = ctx["comment_kind"] == "shotlist"
                if not version and not is_shotlist:
                    raise ValueError("Save a text version before commenting.")
                text_id = params.get("text_version") or None
                shot_id = params.get("shotlist_version") or None
                state = params.get("shotlist_state") if is_shotlist and not shot_id else None
                if not text_id and not shot_id and not state:
                    raise ValueError("Choose a saved version before commenting.")
                # Service validates that replies stay in the same document. Root requests must match this panel too.
                from apps.jrc_texts.models import TextVersion

                from .models import ShotlistVersion

                target_text = (
                    get_object_or_404(TextVersion, pk=text_id, document__post=post, state="final") if text_id else None
                )
                target_shot = get_object_or_404(ShotlistVersion, pk=shot_id, post=post) if shot_id else None
                expected = (
                    ("shotlist", ctx["comment_format"]) if is_shotlist else service.version_section(version, None)
                )
                if (text_id or shot_id) and service.version_section(target_text, target_shot) != expected:
                    raise ValueError("Choose a version from this section.")
                service.create_comment(
                    post,
                    request.user,
                    params.get("body", ""),
                    visibility=params.get("visibility"),
                    parent_id=root.pk if root else None,
                    text_version_id=text_id,
                    shotlist_version_id=shot_id,
                    version_revision=params.get("revision") or None,
                    shotlist_state=state,
                    shotlist_format=ctx["comment_format"],
                )
            else:
                raise ValueError("Choose a valid comment action.")
            ctx = panel_context(
                post,
                request.user,
                ctx["comment_kind"],
                platform_post=ctx["comment_output"],
                output_format=ctx["comment_format"],
            )
    except PermissionError as exc:
        raise PermissionDenied(str(exc)) from exc
    except (ValueError, ValidationError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return render(request, "jrc_production/_version_comments.html", ctx)


@login_required
@never_cache
@require_http_methods(["GET"])
def version(request, workspace_id, post_id, comment_id):
    post = get_object_or_404(Post, pk=post_id, workspace_id=workspace_id, trashed_at__isnull=True)
    content_membership(post.workspace, request.user)
    comment = get_object_or_404(PostComment, pk=comment_id, post=post)
    try:
        return JsonResponse(service.read_comment_version(comment, request.user))
    except (ValueError, PermissionError) as exc:
        raise Http404(str(exc)) from exc
