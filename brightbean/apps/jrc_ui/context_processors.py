# JR Content: přidáno 2026-10-23 (blok F2-B10), AGPL-3.0.

from .features import MVP_FEATURES, enabled


def ui_context(request):
    return {
        "jrc_features": {feature: enabled(feature) for feature in MVP_FEATURES},
        "jrc_app_name": "JR Content",
        "jrc_logo": "img/jrc-logo.svg",
    }


def sidebar_context(request):
    from apps.common.context_processors import sidebar_context as upstream_sidebar

    context = upstream_sidebar(request)
    if not enabled("connections"):
        context["sidebar_connectable_platforms"] = []
    if not enabled("reviews"):
        context["show_review_banner"] = False
    return context


def onboarding_checklist(request):
    if not enabled("onboarding"):
        return {}
    from apps.onboarding.context_processors import onboarding_checklist as upstream_checklist

    return upstream_checklist(request)
