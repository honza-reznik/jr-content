# JR Content: přidáno 2026-10-24 (blok F2-B05), AGPL-3.0.
"""Private S3 media for JR Content.

The web and worker reach Garage on the private Docker network. Browser URLs
identify application resources and contain no credentials. The application
checks the current session and the owning database row before reading bytes.
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
        from django.urls import reverse

        # An object identifier, never a bearer credential. Every GET checks the
        # current Django session and the database row owning this exact key.
        return reverse("jrc_private_file", kwargs={"path": clean_name(name)})
