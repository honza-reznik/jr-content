# JR Content: přidáno 2026-10-23 (blok F2-B03), AGPL-3.0.
"""Serve local library files only to members of their owning workspace/org."""

import posixpath

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import Http404
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe
from django.views.static import serve

from apps.members.models import OrgMembership, WorkspaceMembership

from .models import MediaAsset


@never_cache
@login_required
@require_safe
def private_media(request, path, document_root):
    # Match the exact stored key; never authorize a directory or a normalized
    # alias to another file. Unknown/orphaned uploads are private by default.
    if "\\" in path or posixpath.normpath(path) != path:
        raise Http404

    workspaces = WorkspaceMembership.objects.filter(user=request.user).values("workspace_id")
    organizations = OrgMembership.objects.filter(user=request.user).values("organization_id")
    access = Q(workspace_id__in=workspaces) | Q(workspace__isnull=True, organization_id__in=organizations)
    files = Q(file=path) | Q(thumbnail=path) | Q(versions__file=path) | Q(versions__thumbnail=path)
    if not MediaAsset.objects.filter(access).filter(files).exists():
        raise Http404

    return serve(request, path, document_root=document_root)
