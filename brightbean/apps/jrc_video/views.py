"""Browser SSO entry points (F5-B03). Never put tickets in query strings or logs."""

import hashlib
import secrets
import uuid

from django.conf import settings
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from apps.members.models import WorkspaceMembership

from .models import VideoAsset, VideoProject
from .tickets import TicketError, is_safe_next, issue_ticket


def _uuid(value):
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def target_workspace_ids(next_path):
    """Workspaces whose video the FreeFrame page ``next_path`` opens (P1-B30).

    ``/projects/<id>/...`` names a synchronized client project and ``.../assets/<id>`` a channel video.
    Other pages (the project list, notifications, settings) show only what FreeFrame already limits
    to the person's own projects. An id Brightbean does not know stays for FreeFrame to answer.
    """
    parts = next_path.split("?", 1)[0].strip("/").split("/")
    project_id = asset_id = None
    if parts[0] == "projects" and len(parts) > 1:
        project_id = _uuid(parts[1])
        if len(parts) > 3 and parts[2] == "assets":
            asset_id = _uuid(parts[3])
    elif parts[0] == "assets" and len(parts) > 1:
        asset_id = _uuid(parts[1])
    ids = set()
    if project_id:
        ids.update(VideoProject.objects.filter(ff_project_id=project_id).values_list("workspace_id", flat=True))
    if asset_id:
        ids.update(
            VideoAsset.objects.filter(ff_asset_id=asset_id).values_list("platform_post__post__workspace_id", flat=True)
        )
    return ids


def has_video_access(user, next_path):
    """The same rule FreeFrame applies after sign-in: an active membership in every client the page shows.

    Synchronization (``services.sync_workspace``) makes exactly these members project members in
    FreeFrame. Checking it here gives a refused person one clear answer instead of a FreeFrame page
    that waits for data it is not allowed to load ("Loading asset...").
    """
    memberships = WorkspaceMembership.objects.filter(user=user, workspace__is_archived=False)
    if not memberships.exists():
        return False
    needed = target_workspace_ids(next_path)
    return not needed or memberships.filter(workspace_id__in=needed).count() == len(needed)


def _no_access(request):
    return render(request, "jrc_video/sso.html", {"denied": True}, status=403)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
@csrf_protect
def video_sso(request):
    source = request.POST if request.method == "POST" else request.GET
    next_path = source.get("next", "/projects")
    if not is_safe_next(next_path):
        return HttpResponse("Invalid video destination.", status=400)
    if not has_video_access(request.user, next_path):
        return _no_access(request)
    if request.method == "GET":
        return render(request, "jrc_video/sso.html", {"next_path": next_path})
    try:
        ticket = issue_ticket(request.user, audience="web", next_path=next_path)
    except TicketError:
        return render(request, "jrc_video/sso.html", {"unavailable": True}, status=503)
    response = HttpResponseRedirect(getattr(settings, "JRC_BASE_PATH", "") + "/video/jrc-sso#ticket=" + ticket)
    # A digest binds the fragment to this browser without exposing the ticket
    # in Cookie headers. JS reads it only at the trusted same-origin SSO page.
    response.set_cookie(
        "jrc_sso_binding",
        hashlib.sha256(ticket.encode("ascii")).hexdigest(),
        max_age=60,
        path=getattr(settings, "JRC_BASE_PATH", "") + "/video",
        secure=request.is_secure(),
        samesite="Strict",
    )
    response["Referrer-Policy"] = "no-referrer"
    return response


@never_cache
@require_http_methods(["GET", "POST"])
@csrf_protect
def video_logout(request):
    if request.method == "GET":
        return render(request, "jrc_video/logout.html")
    logout(request)
    grant = secrets.token_urlsafe(32)
    response = HttpResponseRedirect(getattr(settings, "JRC_BASE_PATH", "") + "/video/jrc-logout#grant=" + grant)
    response.set_cookie(
        "jrc_logout_binding",
        hashlib.sha256(grant.encode("ascii")).hexdigest(),
        max_age=60,
        path=getattr(settings, "JRC_BASE_PATH", "") + "/video",
        secure=request.is_secure(),
        samesite="Strict",
    )
    response.delete_cookie("jrc_sso_binding", path=getattr(settings, "JRC_BASE_PATH", "") + "/video", samesite="Strict")
    response["Referrer-Policy"] = "no-referrer"
    return response
