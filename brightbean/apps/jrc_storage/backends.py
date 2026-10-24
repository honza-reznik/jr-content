# JR Content: přidáno 2026-10-24 (blok F2-B05), AGPL-3.0.
"""Private S3 media for JR Content.

The web and worker containers reach Garage inside the Docker network
(``AWS_S3_ENDPOINT_URL``, e.g. ``http://jrc-s3:3900``), but the browser can only
open the local S3 gate (``JRC_S3_PUBLIC_URL``, e.g. ``http://127.0.0.1:13900``).
A SigV4 signature covers the Host header, so a URL signed for the internal host
cannot simply be rewritten: presigned URLs are created by a second client whose
endpoint is the browser-facing one. Signing is local (no network request).
"""

import threading

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from storages.backends.s3boto3 import S3Boto3Storage
from storages.utils import clean_name


class JrcS3Storage(S3Boto3Storage):
    def __init__(self, **kwargs):
        public = kwargs.pop("public_endpoint_url", None) or getattr(settings, "JRC_S3_PUBLIC_URL", "")
        if not public:
            raise ImproperlyConfigured("JRC_S3_PUBLIC_URL is required for browser-facing signed URLs.")
        self.public_endpoint_url = public.rstrip("/")
        super().__init__(**kwargs)
        if not self.querystring_auth:
            # Media are private; an unsigned URL would only ever answer 403.
            raise ImproperlyConfigured("JrcS3Storage requires AWS_QUERYSTRING_AUTH = True.")
        self._public_connections = threading.local()

    @property
    def public_connection(self):
        connection = getattr(self._public_connections, "connection", None)
        if connection is None:
            connection = self._create_session().resource(
                "s3",
                region_name=self.region_name,
                use_ssl=self.use_ssl,
                endpoint_url=self.public_endpoint_url,
                config=self.client_config,
                verify=self.verify,
            )
            self._public_connections.connection = connection
        return connection

    def url(self, name, parameters=None, expire=None, http_method=None):
        params = parameters.copy() if parameters else {}
        params["Bucket"] = self.bucket_name
        params["Key"] = self._normalize_name(clean_name(name))
        return self.public_connection.meta.client.generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=self.querystring_expire if expire is None else expire,
            HttpMethod=http_method,
        )
