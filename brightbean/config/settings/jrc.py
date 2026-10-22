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
