# JR Content: přidáno 2026-10-23 (blok F2-B10), AGPL-3.0.
# JR Content: upraveno 2026-11-04 (blok P1-B11): souhlas s podmínkami jen při zapnuté funkci.
from django.http import HttpResponseNotFound

from apps.accounts.middleware import TosAcceptanceMiddleware

from .features import enabled, path_enabled


class FeatureMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Před přihlášením i CSRF: vypnutý view se nesmí spustit.
        if not path_enabled(request.path_info):
            return HttpResponseNotFound("Page not found")
        return self.get_response(request)


class TermsAcceptanceMiddleware(TosAcceptanceMiddleware):
    """Upstream přesměrování na souhlas s podmínkami, jen když je funkce ``terms`` zapnutá."""

    def __call__(self, request):
        if not enabled("terms"):
            return self.get_response(request)
        return super().__call__(request)
