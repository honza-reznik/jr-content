# JR Content: přidáno 2026-10-23 (blok F2-B10), AGPL-3.0.
"""Vypínače rozhraní; upstream testy mohou jednotlivé funkce znovu povolit."""

from django.conf import settings

MVP_FEATURES = dict.fromkeys(
    (
        "inbox",
        "analytics",
        "reviews",
        "onboarding",
        "ideas",
        "client_portal",
        "intelligence",
        "connections",
        "external_login",
        "unsplash",
        "rss",
    ),
    False,
)


def enabled(feature):
    return getattr(settings, "JRC_FEATURES", {}).get(feature, True)


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
