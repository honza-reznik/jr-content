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
