# JR Content: přidáno 2026-10-23 (blok F2-B10), AGPL-3.0.
from django.http import HttpResponseNotFound

from .features import path_enabled


class FeatureMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Před přihlášením i CSRF: vypnutý view se nesmí spustit.
        if not path_enabled(request.path_info):
            return HttpResponseNotFound("Page not found")
        return self.get_response(request)
