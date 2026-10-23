# JR Content: přidáno 2026-10-22 (blok F2-B01), AGPL-3.0.
import os

from .jrc_offline import JRC_APPS

# isort: split
# Pořadí je záměrné: base.py může při importu inicializovat Sentry.
from .base import *  # noqa: F401, F403, E402

INSTALLED_APPS = [*INSTALLED_APPS, *JRC_APPS]  # noqa: F405
DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]", "jrc-bb-web"]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "brightbean",
        "USER": env("DB_USER"),  # noqa: F405
        "PASSWORD": env("DB_PASSWORD"),  # noqa: F405
        "HOST": env("DB_HOST"),  # noqa: F405
        "PORT": env.int("DB_PORT", default=5432),  # noqa: F405
    },
}
ENCRYPTION_KEY_SALT = env("ENCRYPTION_KEY_SALT").encode("utf-8")  # noqa: F405
EMAIL_BACKEND_TYPE = "console"
EMAIL_INNER_BACKEND = "django.core.mail.backends.console.EmailBackend"
SESSION_COOKIE_SECURE = False
CSP_REPORT_ONLY = True
STORAGES["staticfiles"] = {  # noqa: F405
    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
}
JRC_SOCIAL_PROVIDERS_ENABLED = False

# JR Content: upraveno 2026-10-23 (blok F2-B10), AGPL-3.0.
from apps.jrc_ui.features import configure_ui  # noqa: E402

configure_ui(globals())
from apps.jrc_ui.features import MVP_FEATURES  # noqa: E402

JRC_FEATURES = MVP_FEATURES.copy()

# Stránky smějí načítat obsah jen z místní aplikace (včetně obrázků a médií).
CSP_REPORT_ONLY = False
CSP_SCRIPT_SRC = ("'self'", "'unsafe-eval'")
CSP_STYLE_SRC = ("'self'", "'unsafe-inline'")
CSP_IMG_SRC = ("'self'", "data:", "blob:")
CSP_MEDIA_SRC = ("'self'", "blob:")
CSP_CONNECT_SRC = ("'self'",)
CSP_FORM_ACTION = ("'self'",)

# JR Content: upraveno 2026-10-24 (blok F2-B05), AGPL-3.0.
# Média v soukromém bucketu Garage jrc-app. Server mluví s jrc-s3 ve vnitřní síti,
# prohlížeč dostává krátkodobé podepsané adresy na místní bránu S3.
from urllib.parse import urlsplit  # noqa: E402

STORAGE_BACKEND = "s3"
SERVE_MEDIA = False  # /media/ se už neobsluhuje; místní svazek zůstává jen jako zdroj kopie.
STORAGES["default"] = {"BACKEND": "apps.jrc_storage.backends.JrcS3Storage"}  # noqa: F405
AWS_S3_ENDPOINT_URL = os.environ["JRC_S3_ENDPOINT_URL"]
JRC_S3_PUBLIC_URL = os.environ["JRC_S3_PUBLIC_URL"].rstrip("/")
AWS_ACCESS_KEY_ID = os.environ["JRC_S3_ACCESS_KEY_ID"]
AWS_SECRET_ACCESS_KEY = os.environ["JRC_S3_SECRET_ACCESS_KEY"]
AWS_STORAGE_BUCKET_NAME = os.environ["JRC_S3_BUCKET"]
AWS_S3_REGION_NAME = "garage"
AWS_S3_ADDRESSING_STYLE = "path"
AWS_S3_SIGNATURE_VERSION = "s3v4"
AWS_S3_CUSTOM_DOMAIN = None
AWS_S3_FILE_OVERWRITE = False
# Garage ACL nepodporuje; bucket je soukromý sám o sobě (bez veřejného webu).
AWS_DEFAULT_ACL = None
AWS_QUERYSTRING_AUTH = True
AWS_QUERYSTRING_EXPIRE = 900  # 15 minut; stránka při dalším načtení podepíše znovu.
AWS_S3_MAX_MEMORY_SIZE = 2 * 1024 * 1024
AWS_S3_OBJECT_PARAMETERS = {"CacheControl": "private, max-age=900"}
# Podepsaný POST by mířil na vnitřní adresu jrc-s3; nástroje MCP pro přímé nahrání jsou vypnuté.
MEDIA_LIBRARY_PRESIGNED_POST_SUPPORTED = False

_s3 = urlsplit(JRC_S3_PUBLIC_URL)
JRC_S3_PUBLIC_ORIGIN = f"{_s3.scheme}://{_s3.netloc}"
CSP_IMG_SRC = (*CSP_IMG_SRC, JRC_S3_PUBLIC_ORIGIN)
CSP_MEDIA_SRC = (*CSP_MEDIA_SRC, JRC_S3_PUBLIC_ORIGIN)
CSP_CONNECT_SRC = (*CSP_CONNECT_SRC, JRC_S3_PUBLIC_ORIGIN)

# Internal service credentials are read only from the container environment.
JRC_FF_API_URL = "http://jrc-ff-api:8000"
JRC_FF_SYNC_EMAIL = os.environ.get("JRC_FF_SYNC_EMAIL", "")
JRC_FF_SYNC_PASSWORD = os.environ.get("JRC_FF_SYNC_PASSWORD", "")
JRC_FF_SYNC_ENABLED = bool(JRC_FF_SYNC_EMAIL and JRC_FF_SYNC_PASSWORD)
