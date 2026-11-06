"""Template affordances use the same policy as comment services."""

from django import template

from apps.members.permissions import can_comment

register = template.Library()
register.filter("may_comment", can_comment)
