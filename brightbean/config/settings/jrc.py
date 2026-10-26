# JR Content: přidáno 2026-10-22 (blok F2-B01), AGPL-3.0.
import os
import re

from .jrc_offline import JRC_APPS

# isort: split
# Pořadí je záměrné: base.py může při importu inicializovat Sentry.
from .base import *  # noqa: F401, F403, E402

# jrc_test imports the local-storage settings from test.py, which in turn
# shares this mapping with base.py. Keep the runtime S3 selection isolated so
# importing both settings modules cannot replace the test suite's backend.
STORAGES = dict(STORAGES)  # noqa: F405

INSTALLED_APPS = [*INSTALLED_APPS, *JRC_APPS]  # noqa: F405
DEBUG = os.environ.get("JRC_MODE", "prod") == "dev"
# JR Content is an invite-only local workspace; do not inherit Brightbean's
# public-registration default from base.py.
SIGNUP_MODE = "invite"
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
# JR Content: upraveno 2026-10-25 (blok F7-B03): upozornění neposílají webhooky ven.
NOTIFICATION_WEBHOOKS_ENABLED = False

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
# F5-B02: shared HMAC key for FreeFrame sign-in tickets (only jrc-bb-web has it).
JRC_FF_SSO_SECRET = os.environ.get("JRC_FF_SSO_SECRET", "")
# F5-B05: largest video accepted by "Upload version" (bytes); FreeFrame enforces the same cap.
JRC_VIDEO_MAX_UPLOAD_BYTES = int(os.environ.get("JRC_VIDEO_MAX_UPLOAD_BYTES", str(4 * 1024**3)))

# P1-B01: veřejná adresa a montáž pod předponou; kořen je platná volba.
JRC_PUBLIC_ORIGIN = os.environ.get("JRC_PUBLIC_ORIGIN", "http://127.0.0.1:13000").rstrip("/")
JRC_BASE_PATH = os.environ.get("JRC_BASE_PATH", "/jrc").rstrip("/")
_public = urlsplit(JRC_PUBLIC_ORIGIN)
if (
    _public.scheme not in ("http", "https")
    or not _public.hostname
    or _public.path
    or _public.query
    or _public.fragment
    or _public.username
    or _public.password
):
    raise ValueError("JRC_PUBLIC_ORIGIN musí být http(s) původ bez cesty")
if JRC_BASE_PATH and not re.fullmatch(r"(?:/[A-Za-z0-9_-]+)+", JRC_BASE_PATH):
    raise ValueError("JRC_BASE_PATH musí být prázdné nebo /cesta bez koncového lomítka")
# APP_URL je původ; reverse() už předponu přidává i mimo HTTP (e-maily, úlohy).
APP_URL = JRC_PUBLIC_ORIGIN
MCP_PUBLIC_BASE_URL = MCP_OAUTH_ISSUER_URL = JRC_PUBLIC_ORIGIN + JRC_BASE_PATH
FORCE_SCRIPT_NAME = JRC_BASE_PATH or None
STATIC_URL = JRC_BASE_PATH + "/static/"
WHITENOISE_STATIC_PREFIX = "/static/"
LOGIN_URL = "account_login"
LOGIN_REDIRECT_URL = JRC_BASE_PATH + "/"
LOGOUT_REDIRECT_URL = JRC_BASE_PATH + "/accounts/login/"
ACCOUNT_LOGOUT_REDIRECT_URL = LOGOUT_REDIRECT_URL
SESSION_COOKIE_PATH = CSRF_COOKIE_PATH = JRC_BASE_PATH + "/"
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = _public.scheme == "https"
ALLOWED_HOSTS = [_public.hostname, "127.0.0.1", "localhost", "jrc-bb-web"]
CSRF_TRUSTED_ORIGINS = [JRC_PUBLIC_ORIGIN]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 31536000 if SESSION_COOKIE_SECURE else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_PRELOAD = False
MIDDLEWARE = ["apps.jrc_ui.proxy.TrustedProxyMiddleware", *MIDDLEWARE]  # noqa: F405
JRC_S3_PUBLIC_PATH = os.environ.get("JRC_S3_PUBLIC_PATH", "")

# Shared cache for authentication and API budgets across gunicorn processes.
CACHES["default"] = {  # noqa: F405
    "BACKEND": "django.core.cache.backends.redis.RedisCache",
    "LOCATION": os.environ.get("JRC_CACHE_REDIS_URL", "redis://jrc-redis:6379/1"),
    "OPTIONS": {"socket_connect_timeout": 1, "socket_timeout": 1},
}

# Celkový REST/MCP rozpočet, sdílený přes výše uvedenou Redis cache.
BB_API_LIMIT = 60000
