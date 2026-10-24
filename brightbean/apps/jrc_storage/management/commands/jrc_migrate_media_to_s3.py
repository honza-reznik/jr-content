# JR Content: přidáno 2026-10-24 (blok F2-B05), AGPL-3.0.
"""One-time copy of media from the local volume (MEDIA_ROOT) into the S3 bucket.

* An object that already exists in S3 is never overwritten: after the switch S3
  is the live copy, the local volume only the retained backup.
* Completion is recorded as a marker object in the bucket, written only after a
  full, error-free walk. Once it exists the command copies nothing, so a later
  ``make up`` can never restore replaced or deleted media from the old volume.
* An interrupted run leaves no marker; the next run continues (objects already
  copied are skipped, the rest is copied).
* Local files are never deleted or modified.
"""

import json
import mimetypes
import os
from pathlib import Path

from botocore.exceptions import ClientError
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.jrc_storage.s3 import client_and_bucket

# Outside every upload_to prefix of the app, so it cannot collide with media.
MARKER_KEY = ".jrc/media-migration-complete.json"
# Operational files of JR Content in the volume root; never media.
SKIPPED_ROOT_FILES = frozenset({".jrc-working-dir"})


def _remote_size(client, bucket, key):
    try:
        return client.head_object(Bucket=bucket, Key=key)["ContentLength"]
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return None
        raise


def _walk_error(error):
    raise CommandError(f"Cannot read {error.filename}: {error.strerror}. Migration not completed.")


def iter_local_media(root):
    """Regular files below root as (path, key). Unreadable directories abort the walk."""
    for directory, dirnames, filenames in os.walk(root, onerror=_walk_error):
        dirnames.sort()
        for filename in sorted(filenames):
            path = Path(directory, filename)
            key = path.relative_to(root).as_posix()
            if key in SKIPPED_ROOT_FILES or path.is_symlink() or not path.is_file():
                continue
            yield path, key


class Command(BaseCommand):
    help = "Copy local Brightbean media into S3 once; never overwrites S3, keeps local files."

    def add_arguments(self, parser):
        parser.add_argument("--source", default=os.environ.get("MEDIA_ROOT", ""), help="Local media directory.")

    def handle(self, *args, source, **options):
        client, bucket = client_and_bucket()
        if _remote_size(client, bucket, MARKER_KEY) is not None:
            self.stdout.write(f"Media migration to S3 bucket {bucket} already completed; nothing copied.")
            return
        if not source:
            raise CommandError("No media source directory (set MEDIA_ROOT or --source).")
        root = Path(source)
        extra = dict(getattr(settings, "AWS_S3_OBJECT_PARAMETERS", {}))
        copied = present = differs = 0
        if root.is_dir():
            for path, key in iter_local_media(root):
                size = path.stat().st_size
                remote = _remote_size(client, bucket, key)
                if remote is not None:
                    present += 1
                    if remote != size:
                        differs += 1
                        self.stderr.write(f"Kept S3 object {key}: differs from the local copy (not overwritten).")
                    continue
                args = {**extra, "ContentType": mimetypes.guess_type(key)[0] or "application/octet-stream"}
                client.upload_file(str(path), bucket, key, ExtraArgs=args)
                if _remote_size(client, bucket, key) != size:
                    raise CommandError(f"Copy of {key} did not verify (size differs). Migration not completed.")
                copied += 1
        summary = {"completed_at": timezone.now().isoformat(), "copied": copied, "present": present}
        client.put_object(
            Bucket=bucket, Key=MARKER_KEY, Body=json.dumps(summary).encode(), ContentType="application/json"
        )
        self.stdout.write(
            f"Media to S3 bucket {bucket}: copied {copied}, already present {present} "
            f"({differs} differing kept); migration completed, local files kept."
        )
