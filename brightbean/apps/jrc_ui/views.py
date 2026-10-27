# JR Content: přidáno 2026-10-23 (blok F2-B10), AGPL-3.0.
import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import render


def about(request):
    return render(request, "jrc_ui/about.html")


def license_text(request):
    return FileResponse((settings.BASE_DIR / "LICENSE").open("rb"), content_type="text/plain; charset=utf-8")


@login_required
def source_code(request):
    version = getattr(settings, "JRC_SOURCE_VERSION", "")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", version):
        raise Http404
    archive = getattr(settings, "JRC_SOURCE_ARCHIVE_DIR", Path("/opt/jrc-source")) / f"jr-content-{version}.tar.gz"
    try:
        file = archive.open("rb")
    except OSError as exc:
        raise Http404 from exc
    return FileResponse(
        file,
        as_attachment=True,
        filename=f"jr-content-{version}.tar.gz",
        content_type="application/gzip",
    )
