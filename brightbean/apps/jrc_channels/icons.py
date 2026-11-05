"""Small local raster icons, decoded and re-encoded before private storage."""

from io import BytesIO
from pathlib import Path
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, UnidentifiedImageError

MAX_BYTES = 1024 * 1024
MAX_PIXELS = 4_000_000
ERROR = "Upload a PNG, JPG or WebP image, at most 1 MB and 4 million pixels."
FORMATS = {"PNG": {".png"}, "JPEG": {".jpg", ".jpeg"}, "WEBP": {".webp"}}
TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


def icon_path(instance, filename):
    return f"jrc_channel_icons/{instance.social_account_id}/{uuid4().hex}.png"


def validated_icon(upload):
    if not upload or not 0 < upload.size <= MAX_BYTES:
        raise ValidationError(ERROR)
    try:
        with Image.open(upload) as image:
            if (
                image.format not in FORMATS
                or Path(upload.name).suffix.lower() not in FORMATS[image.format]
                or upload.content_type != TYPES[image.format]
                or image.width * image.height > MAX_PIXELS
            ):
                raise ValidationError(ERROR)
            image.load()
            image.thumbnail((128, 128))
            # Fresh pixels drop metadata and any trailing payload from the uploaded file.
            clean = Image.new("RGBA", image.size)
            clean.paste(image.convert("RGBA"))
            output = BytesIO()
            clean.save(output, format="PNG")
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError):
        raise ValidationError(ERROR) from None
    return ContentFile(output.getvalue(), name="icon.png")
