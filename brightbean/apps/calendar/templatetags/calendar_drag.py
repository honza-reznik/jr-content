from django import template

from apps.calendar.drag import drop_refusal

register = template.Library()


@register.filter
def can_drop(pp, membership):
    """Is this calendar chip offered as draggable? The same rule as the drop endpoint (P1-B34)."""
    return drop_refusal(pp, membership) is None
