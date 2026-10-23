# JR Content: přidáno 2026-10-24 (blok F2-B05), AGPL-3.0.
"""Server-side helpers for the jrc-app bucket (internal endpoint, never logs secrets)."""

from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.storage import default_storage


def client_and_bucket():
    """The storage's own boto3 client (internal endpoint) and bucket name."""
    if not hasattr(default_storage, "bucket_name"):
        raise ImproperlyConfigured("Default storage is not S3 (STORAGE_BACKEND must be s3).")
    return default_storage.connection.meta.client, default_storage.bucket_name


def app_origin():
    """Browser origin of the application (scheme://host:port), from APP_URL."""
    parts = urlsplit(settings.APP_URL)
    if not parts.scheme or not parts.netloc:
        raise ImproperlyConfigured("APP_URL must be an absolute URL.")
    return f"{parts.scheme}://{parts.netloc}"


def cors_rules(origin):
    """Only the app origin may read media cross-origin (video frame picker, image editor)."""
    return [
        {
            "AllowedOrigins": [origin],
            "AllowedMethods": ["GET", "HEAD"],
            "AllowedHeaders": ["Range"],
            "ExposeHeaders": ["Content-Length", "Content-Range", "Content-Type", "ETag"],
            "MaxAgeSeconds": 3600,
        }
    ]
