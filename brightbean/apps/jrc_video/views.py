"""Browser SSO entry points (F5-B03). Never put tickets in query strings or logs."""

import hashlib

from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from apps.members.models import WorkspaceMembership

from .tickets import TicketError, is_safe_next, issue_ticket


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
@csrf_protect
def video_sso(request):
    source = request.POST if request.method == "POST" else request.GET
    next_path = source.get("next", "/projects")
    if not is_safe_next(next_path):
        return HttpResponse("Invalid video destination.", status=400)
    if not WorkspaceMembership.objects.filter(user=request.user, workspace__is_archived=False).exists():
        return HttpResponse("You do not have access to video.", status=403)
    if request.method == "GET":
        return render(request, "jrc_video/sso.html", {"next_path": next_path})
    try:
        ticket = issue_ticket(request.user, audience="web", next_path=next_path)
    except TicketError:
        return render(request, "jrc_video/sso.html", {"unavailable": True}, status=503)
    response = HttpResponseRedirect("/video/jrc-sso#ticket=" + ticket)
    # A digest binds the fragment to this browser without exposing the ticket
    # in Cookie headers. JS reads it only at the trusted same-origin SSO page.
    response.set_cookie(
        "jrc_sso_binding",
        hashlib.sha256(ticket.encode("ascii")).hexdigest(),
        max_age=60,
        path="/video",
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
    response = HttpResponseRedirect("/video/jrc-logout")
    response.delete_cookie("jrc_sso_binding", path="/video", samesite="Strict")
    return response
