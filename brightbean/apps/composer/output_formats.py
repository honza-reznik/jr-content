# JR Content: přidáno 2026-11-14 (blok P2-B14), AGPL-3.0.
"""Output formats: one channel may carry several outputs of one content, one per format.

An *output* is a channel copy (``PlatformPost``) plus its format, e.g. YouTube · Video next to
YouTube · Shorts, or Instagram · Reel next to Instagram · Story. Each output keeps its own video,
caption, thumbnail and publication date because each is its own ``PlatformPost``.

The stored value is generic (long form, short form, story, post), so production can group the work
by format (the shotlist has one list per format) while each platform shows its own word for it
(a YouTube short form is "Shorts", an Instagram one is a "Reel").
"""

from django.db import models


class OutputFormat(models.TextChoices):
    LONG_FORM = "long_form", "Long form"
    SHORT_FORM = "short_form", "Short form"
    STORY = "story", "Story"
    POST = "post", "Post"


# Display order of the formats (the shotlist sub-tabs and the output switchers follow it).
FORMAT_ORDER = [OutputFormat.LONG_FORM, OutputFormat.SHORT_FORM, OutputFormat.STORY, OutputFormat.POST]

# Formats each platform offers, in display order, with the platform's own name for the format.
# The first one is the default of a new output on that platform. Platforms not listed offer every
# format under its generic name (custom channels such as Forendors), defaulting to a post.
PLATFORM_FORMATS = {
    "youtube": {OutputFormat.LONG_FORM: "Video", OutputFormat.SHORT_FORM: "Shorts"},
    "instagram": {OutputFormat.SHORT_FORM: "Reel", OutputFormat.STORY: "Story", OutputFormat.POST: "Post"},
    "instagram_login": {OutputFormat.SHORT_FORM: "Reel", OutputFormat.STORY: "Story", OutputFormat.POST: "Post"},
    "facebook": {
        OutputFormat.POST: "Post",
        OutputFormat.LONG_FORM: "Video",
        OutputFormat.SHORT_FORM: "Reel",
        OutputFormat.STORY: "Story",
    },
    "tiktok": {OutputFormat.SHORT_FORM: "Video"},
    "linkedin_personal": {OutputFormat.POST: "Post", OutputFormat.LONG_FORM: "Video"},
    "linkedin_company": {OutputFormat.POST: "Post", OutputFormat.LONG_FORM: "Video"},
}

_GENERIC = {
    OutputFormat.POST: "Post",
    OutputFormat.LONG_FORM: "Long form",
    OutputFormat.SHORT_FORM: "Short form",
    OutputFormat.STORY: "Story",
}

# The upstream ``PostType`` a format asks the publish engine for, per platform. Only where the
# platform has a distinct publishing path; everything else is left to the attached media.
_POST_TYPE_HINTS = {
    ("youtube", OutputFormat.LONG_FORM): "video",
    ("youtube", OutputFormat.SHORT_FORM): "short",
    ("instagram", OutputFormat.SHORT_FORM): "reel",
    ("instagram", OutputFormat.STORY): "story",
    ("instagram_login", OutputFormat.SHORT_FORM): "reel",
    ("instagram_login", OutputFormat.STORY): "story",
    ("facebook", OutputFormat.SHORT_FORM): "reel",
    ("facebook", OutputFormat.STORY): "story",
    ("facebook", OutputFormat.LONG_FORM): "video",
}

# Upstream ``platform_extra["post_type"]`` values of existing copies, mapped to a format when the
# copies become outputs (migration 0025). Anything else takes the platform default.
_POST_TYPE_TO_FORMAT = {
    "short": OutputFormat.SHORT_FORM,
    "reel": OutputFormat.SHORT_FORM,
    "story": OutputFormat.STORY,
    "video": OutputFormat.LONG_FORM,
    "image": OutputFormat.POST,
    "carousel": OutputFormat.POST,
    "text": OutputFormat.POST,
    "link": OutputFormat.POST,
}


def formats_for(platform):
    """``{format: platform name}`` of the formats a platform offers, default first."""
    return dict(PLATFORM_FORMATS.get(platform, _GENERIC))


def default_format(platform):
    return next(iter(formats_for(platform)))


def is_allowed(platform, output_format):
    return output_format in formats_for(platform)


def format_name(platform, output_format):
    """The platform's word for the format ("Shorts", "Reel"), or the generic one."""
    return formats_for(platform).get(output_format) or OutputFormat(output_format).label


def format_sort_key(output_format):
    try:
        return FORMAT_ORDER.index(output_format)
    except ValueError:
        return len(FORMAT_ORDER)


def post_type_hint(platform, output_format):
    """The ``PostType`` value the publish engine should use for this output, or ``None``."""
    return _POST_TYPE_HINTS.get((platform, output_format))


def format_from_post_type(platform, post_type):
    """The format an existing copy had in all but name (its upstream post type), else the default."""
    candidate = _POST_TYPE_TO_FORMAT.get(str(post_type or "").lower())
    if candidate and is_allowed(platform, candidate):
        return candidate
    return default_format(platform)


class OutputFormatField(models.CharField):
    """The output's format; an empty value takes the platform default when the row is saved.

    ``pre_save`` runs for ``save()`` and ``bulk_create()`` alike, so every upstream path that creates a
    copy without knowing about formats (duplicate, recurrence, idea conversion, the API) still stores
    the platform default instead of an empty value that would sit outside the uniqueness rule.
    """

    def pre_save(self, model_instance, add):
        value = getattr(model_instance, self.attname)
        if not value:
            value = default_format(model_instance.social_account.platform)
            setattr(model_instance, self.attname, value)
        return value


def output_reversal_blockers(platform_post_model):
    """Why going back before outputs (migration ``composer.0025``) would lose data; empty when it would not.

    ``platform_post_model`` is the historical ``PlatformPost`` of a migration. Going back restores the
    old one-copy-per-channel rule and drops the format, which the forward migration later re-derives from
    the upstream post type. That loses data when one channel carries several outputs of one content
    (they cannot become one copy) or when an output's format is not the one re-derived (an Instagram
    Story added as an output would come back as a Reel). Checked before anything is dropped (P2-B14).
    """
    from django.db.models import Count

    several = (
        platform_post_model._base_manager.values("post_id", "social_account_id")
        .annotate(outputs=Count("id"))
        .filter(outputs__gt=1)
        .count()
    )
    rows = platform_post_model._base_manager.values_list(
        "output_format", "platform_extra", "social_account__platform"
    ).iterator(chunk_size=500)
    changed = sum(
        1
        for output_format, extra, platform in rows
        if output_format
        and output_format
        != format_from_post_type(platform, (extra if isinstance(extra, dict) else {}).get("post_type"))
    )
    blockers = []
    if several:
        blockers.append(f"{several} channel(s) carry more than one output of the same content")
    if changed:
        blockers.append(f"{changed} output(s) have a format the old schema cannot keep")
    return blockers
