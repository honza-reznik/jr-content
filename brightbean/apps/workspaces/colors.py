"""Stable workspace accents with readable white labels (WCAG contrast >= 4.5)."""

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
    channels = [int(value[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    luminance = sum(c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True))
    if 1.05 / (luminance + 0.05) < 4.5:
        raise ValidationError("Choose a darker colour: contrast with white must be at least 4.5:1.")
    return value.upper()


def workspace_accent(workspace):
    if workspace.primary_color:
        try:
            return validate_accent(workspace.primary_color)
        except ValidationError:
            pass
    return PALETTE[workspace.pk.int % len(PALETTE)][0]
