# JR Content: přidáno 2026-10-23 (blok F2-B10), AGPL-3.0.
"""Společné vypínače webu, REST API a MCP; vyhodnocují se při každém požadavku."""

import re
from dataclasses import dataclass

from django.conf import settings


@dataclass(frozen=True)
class FeatureSurface:
    path_pattern: str
    api_prefix: str = ""
    mcp_tools: tuple[str, ...] = ()


# Jediné přiřazení vstupních bodů k funkcím pro middleware, API i MCP.
FEATURE_SURFACES = {
    "inbox": FeatureSurface(
        r"^/workspace/[^/]+/inbox(?:/|$)",
        "/api/v1/inbox",
        (
            "list_inbox_messages",
            "get_inbox_message",
            "create_reply_draft",
            "update_reply_draft",
            "discard_reply_draft",
            "send_reply",
        ),
    ),
    "analytics": FeatureSurface(
        r"^/workspace/[^/]+/analytics(?:/|$)",
        "/api/v1/analytics",
        ("get_account_analytics", "get_post_analytics"),
    ),
    "reviews": FeatureSurface(r"^/accounts/review-banner(?:/|$)"),
    "onboarding": FeatureSurface(r"^/onboarding(?:/|$)"),
    "ideas": FeatureSurface(r"^/workspace/[^/]+/(?:create|ideas)(?:/|$)"),
    "client_portal": FeatureSurface(r"^/(?:portal|workspace/[^/]+/settings/clients)(?:/|$)"),
    "intelligence": FeatureSurface(r"^/(?:intelligence|orgs/[^/]+/intelligence)(?:/|$)"),
    "connections": FeatureSurface(
        r"^/social-accounts/(?:callback|select-account|[^/]+/(?:connect|[^/]+/(?:reconnect|retry-webhooks)))(?:/|$)"
    ),
    "unsplash": FeatureSurface(r"^/workspace/[^/]+/compose/(?:[^/]+/)?unsplash-(?:search|import)(?:/|$)"),
    "external_login": FeatureSurface(r"^/accounts/(?:google|3rdparty)(?:/|$)"),
    "rss": FeatureSurface(r"^/workspace/[^/]+/feeds(?:/|$)"),
}
MVP_FEATURES = dict.fromkeys(FEATURE_SURFACES, False)


def enabled(feature):
    return getattr(settings, "JRC_FEATURES", {}).get(feature, True)


def api_path_enabled(path):
    return all(
        enabled(feature)
        for feature, surface in FEATURE_SURFACES.items()
        if surface.api_prefix and (path == surface.api_prefix or path.startswith(surface.api_prefix + "/"))
    )


def path_enabled(path):
    return api_path_enabled(path) and all(
        enabled(feature) for feature, surface in FEATURE_SURFACES.items() if re.match(surface.path_pattern, path)
    )


def mcp_tool_enabled(name):
    return all(enabled(feature) for feature, surface in FEATURE_SURFACES.items() if name in surface.mcp_tools)


def configure_ui(namespace):
    namespace["JRC_UI_ENABLED"] = True
    namespace["MIDDLEWARE"] = ["apps.jrc_ui.middleware.FeatureMiddleware", *namespace["MIDDLEWARE"]]
    processors = namespace["TEMPLATES"][0]["OPTIONS"]["context_processors"]
    replacements = {
        "apps.common.context_processors.sidebar_context": "apps.jrc_ui.context_processors.sidebar_context",
        "apps.onboarding.context_processors.onboarding_checklist": "apps.jrc_ui.context_processors.onboarding_checklist",
    }
    processors[:] = [replacements.get(processor, processor) for processor in processors]
    processors.append("apps.jrc_ui.context_processors.ui_context")
