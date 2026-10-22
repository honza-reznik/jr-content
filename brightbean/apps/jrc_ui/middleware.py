# JR Content: přidáno 2026-10-23 (blok F2-B10), AGPL-3.0.
import re

from django.http import HttpResponseNotFound

from .features import enabled

# Kontrola před přihlášením i RBAC: přímá URL nesmí spustit vypnutý view.
FEATURE_PATHS = {
    "inbox": r"^/(?:workspace/[^/]+/inbox|api/v1/inbox)(?:/|$)",
    "analytics": r"^/(?:workspace/[^/]+/analytics|api/v1/analytics)(?:/|$)",
    "reviews": r"^/accounts/review-banner(?:/|$)",
    "onboarding": r"^/onboarding(?:/|$)",
    "ideas": r"^/workspace/[^/]+/(?:create|ideas)(?:/|$)",
    "client_portal": r"^/(?:portal|workspace/[^/]+/settings/clients)(?:/|$)",
    "intelligence": r"^/(?:intelligence|orgs/[^/]+/intelligence)(?:/|$)",
    "connections": r"^/social-accounts/(?:callback|select-account|[^/]+/(?:connect|[^/]+/(?:reconnect|retry-webhooks)))(?:/|$)",
    "unsplash": r"^/workspace/[^/]+/compose/(?:[^/]+/)?unsplash-(?:search|import)(?:/|$)",
    "external_login": r"^/accounts/(?:google|3rdparty)(?:/|$)",
    "rss": r"^/workspace/[^/]+/feeds(?:/|$)",
}


class FeatureMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        for feature, pattern in FEATURE_PATHS.items():
            if not enabled(feature) and re.match(pattern, request.path_info):
                return HttpResponseNotFound("Page not found")
        return self.get_response(request)
