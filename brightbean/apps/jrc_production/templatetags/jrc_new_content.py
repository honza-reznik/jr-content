# JR Content: přidáno 2026-11-13 (blok P2-B03), AGPL-3.0.
"""Data of the New idea window and the New content link in the menu, on every page with the sidebar."""

from django import template

register = template.Library()

_UNSET = object()


@register.simple_tag(takes_context=True)
def new_content_options(context):
    """Clients the user may create content for; ``fixed`` is the current client when it is clear.

    The current client is the one the page shows. A page that shows a client other than the workspace
    of its address (the board's filter, the New content page) passes it as ``new_content_client``
    ("" = no single client, so the window asks); elsewhere it is the workspace of the page.
    """
    request = context.get("request")
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"clients": [], "fixed": None}
    from apps.jrc_production.views import creation_options

    client = context.get("new_content_client", _UNSET)
    if client is _UNSET:
        workspace = getattr(request, "workspace", None)
        client = str(workspace.id) if workspace else ""
    return creation_options(user, client=client)
