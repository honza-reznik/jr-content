# JR Content: přidáno 2026-10-24 (blok F2-B05), AGPL-3.0.
from django.core.management.base import BaseCommand

from apps.jrc_storage.s3 import app_origin, client_and_bucket, cors_rules


class Command(BaseCommand):
    help = "Set CORS of the private media bucket to the application origin only (idempotent)."

    def handle(self, *args, **options):
        client, bucket = client_and_bucket()
        origin = app_origin()
        client.put_bucket_cors(Bucket=bucket, CORSConfiguration={"CORSRules": cors_rules(origin)})
        self.stdout.write(f"S3 bucket {bucket}: CORS allows only {origin} (GET, HEAD).")
