# JR Content: přidáno 2026-10-24 (blok F2-B05), AGPL-3.0.
"""Copy media from the local volume (MEDIA_ROOT) into the S3 bucket.

Idempotent: an object that already exists with the same size is skipped.
Local files are never deleted or modified, so the copy can be repeated and the
local volume stays a fallback until someone removes it on purpose.
"""

import mimetypes
import os
from pathlib import Path

from botocore.exceptions import ClientError
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.jrc_storage.s3 import client_and_bucket


def _remote_size(client, bucket, key):
    try:
        return client.head_object(Bucket=bucket, Key=key)["ContentLength"]
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return None
        raise


def iter_local_media(root):
    """Regular files below root as (path, key); hidden files and symlinks are skipped."""
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for filename in sorted(filenames):
            path = Path(directory, filename)
            if filename.startswith(".") or path.is_symlink() or not path.is_file():
                continue
            yield path, path.relative_to(root).as_posix()


class Command(BaseCommand):
    help = "Copy local Brightbean media into S3 without deleting local files (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--source", default=os.environ.get("MEDIA_ROOT", ""), help="Local media directory.")

    def handle(self, *args, source, **options):
        if not source:
            raise CommandError("No media source directory (set MEDIA_ROOT or --source).")
        root = Path(source)
        if not root.is_dir():
            self.stdout.write(f"No local media directory {root}; nothing to copy.")
            return
        client, bucket = client_and_bucket()
        extra = dict(getattr(settings, "AWS_S3_OBJECT_PARAMETERS", {}))
        copied = skipped = 0
        for path, key in iter_local_media(root):
            size = path.stat().st_size
            if _remote_size(client, bucket, key) == size:
                skipped += 1
                continue
            args = {**extra, "ContentType": mimetypes.guess_type(key)[0] or "application/octet-stream"}
            client.upload_file(str(path), bucket, key, ExtraArgs=args)
            if _remote_size(client, bucket, key) != size:
                raise CommandError(f"Copy of {key} did not verify (size differs).")
            copied += 1
        self.stdout.write(f"Media to S3 bucket {bucket}: copied {copied}, already present {skipped}; local files kept.")
