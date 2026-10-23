# JR Content: přidáno 2026-10-23 (blok F2-B10), AGPL-3.0.
from django.conf import settings
from django.http import FileResponse
from django.shortcuts import render


def about(request):
    return render(request, "jrc_ui/about.html")


def license_text(request):
    return FileResponse((settings.BASE_DIR / "LICENSE").open("rb"), content_type="text/plain; charset=utf-8")
