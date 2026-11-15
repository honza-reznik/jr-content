from django import template

from apps.jrc_production.version_comments import panel_context

register = template.Library()


@register.inclusion_tag("jrc_production/_version_comments.html", takes_context=True)
def version_comments(context, kind, output="", output_format=""):
    return {
        **panel_context(
            context["post"], context["request"].user, kind, platform_post=str(output or ""), output_format=output_format
        ),
        "request": context["request"],
        "csrf_token": context["csrf_token"],
    }
