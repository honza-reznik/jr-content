"""Stable workspace accents with an automatically readable label colour."""

from django.core.exceptions import ValidationError

from apps.common.validators import validate_hex_color

PALETTE = (
    ("#B91C1C", "Red"),
    ("#C2410C", "Orange"),
    ("#854D0E", "Gold"),
    ("#3F6212", "Lime"),
    ("#047857", "Green"),
    ("#0E7490", "Cyan"),
    ("#1D4ED8", "Blue"),
    ("#6D28D9", "Violet"),
    ("#A21CAF", "Magenta"),
    ("#BE185D", "Pink"),
)


def validate_accent(value):
    validate_hex_color(value)
    return value.upper()


def contrast_ratio(value, foreground):
    """Return WCAG relative contrast for two six-digit hex colours."""

    def luminance(color):
        channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return sum(c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True))

    first, second = sorted((luminance(value), luminance(foreground)), reverse=True)
    return (first + 0.05) / (second + 0.05)


SIDEBAR_SURFACE = "#F0EFEB"


def _mix(value, other, share):
    """sRGB mix like CSS color-mix(): ``share`` of ``other`` into ``value``."""
    channels = [round(int(value[i : i + 2], 16) * (1 - share) + int(other[i : i + 2], 16) * share) for i in (1, 3, 5)]
    return "#{:02X}{:02X}{:02X}".format(*channels)


def accent_link_color(value, minimum=4.5):
    """Darken the accent until it is readable as writing on white and on the soft accent tint.

    This is the single source for ``--primary-text`` (links, headings, active menu text). The
    accent itself stays untouched where it is a background; text placed on it uses
    ``accent_text_color``.
    """
    # Surfaces the text sits on: white, --primary-soft (the accent tinted 94 % to white) and the
    # slightly grey sidebar/hover surface, which is the darkest of them.
    surfaces = ("#FFFFFF", _mix(value, "#FFFFFF", 0.94), SIDEBAR_SURFACE)
    for step in range(101):
        candidate = _mix(value, "#000000", step / 100)
        if all(contrast_ratio(candidate, surface) >= minimum for surface in surfaces):
            return candidate
    return "#000000"


def accent_text_color(value):
    """Pick black or white, whichever gives the stronger WCAG contrast."""
    return "#FFFFFF" if contrast_ratio(value, "#FFFFFF") >= contrast_ratio(value, "#000000") else "#000000"


def workspace_accent(workspace):
    if workspace.primary_color:
        try:
            return validate_accent(workspace.primary_color)
        except ValidationError:
            pass
    return PALETTE[workspace.pk.int % len(PALETTE)][0]
