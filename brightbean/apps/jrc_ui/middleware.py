# JR Content: přidáno 2026-10-23 (blok F2-B10), AGPL-3.0.
# JR Content: upraveno 2026-11-04 (blok P1-B11): souhlas s podmínkami jen při zapnuté funkci.
from django.http import HttpResponseNotFound
from django.shortcuts import render

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


# JR Content: přidáno 2026-11-07 (blok P1-B38), AGPL-3.0.
REJECTED_TITLES = {
    400: "This request cannot be shown",
    403: "You cannot perform this action",
    404: "This page is unavailable",
    405: "This action is not available here",
    409: "This changed in the meantime",
    410: "This is no longer available",
    422: "This request cannot be shown",
}
_MAX_PLAIN_BODY = 1000


class RejectedPageMiddleware:
    """Every rejected page request is answered inside the app layout (P1-B38).

    Views and services reject with a bare ``HttpResponse(text, status=400/403/404/...)`` in many
    places; opened as a page in the browser that is an unstyled line of text with no way back. This
    is the one place that turns such an answer to a *page navigation* into the layout page with the
    message and a way back. Fragment requests (HTMX, ``fetch`` with ``X-Requested-With``, anything
    that does not ask for a document) keep the plain answer their scripts expect, and so do answers
    that already are a full HTML page (the 403/404 templates).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if response.status_code in REJECTED_TITLES and self._is_page_navigation(request) and self._is_bare(response):
            return self._page(request, response)
        return response

    @staticmethod
    def _is_page_navigation(request):
        if request.headers.get("HX-Request") or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return False
        if "text/html" not in request.headers.get("Accept", ""):
            return False
        return request.headers.get("Sec-Fetch-Dest", "document") == "document"

    @staticmethod
    def _is_bare(response):
        if response.streaming or response.has_header("Content-Disposition"):
            return False
        content_type = response.get("Content-Type", "")
        if not content_type.startswith(("text/plain", "text/html")) or len(response.content) > _MAX_PLAIN_BODY:
            return False
        head = response.content[:200].lower()
        return b"<html" not in head and b"<!doctype" not in head

    @staticmethod
    def _page(request, response):
        text = response.content.decode(response.charset or "utf-8", "replace").strip()
        try:
            rendered = render(
                request,
                "jrc_ui/rejected.html",
                {"status": response.status_code, "heading": REJECTED_TITLES[response.status_code], "text": text},
                status=response.status_code,
            )
        except Exception:  # noqa: BLE001 - the page is a courtesy; the original answer still is correct
            return response
        for header in ("Allow", "Content-Range", "WWW-Authenticate", "Retry-After"):
            if response.has_header(header):
                rendered[header] = response[header]
        return rendered
