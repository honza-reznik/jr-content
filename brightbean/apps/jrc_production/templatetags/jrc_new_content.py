# JR Content: přidáno 2026-11-13 (blok P2-B03), upraveno 2026-11-14 (blok P2-B13), AGPL-3.0.
"""Data of the New idea window and the New content link in the menu, on every page with the sidebar."""

from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def new_content_options(context):
    """What may be created in the current workspace (P2-B13): new content always goes there.

    The current workspace is the client the menu shows (``current_membership``); there is no
    client to choose. ``fixed`` is None when the user may not create content in it.
    """
    request = context.get("request")
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"clients": [], "fixed": None}
    from apps.jrc_production.views import creation_options, current_membership

    return creation_options(current_membership(request))
