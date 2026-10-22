# JR Content: přidáno 2026-10-22 (blok F2-B01), AGPL-3.0.
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
