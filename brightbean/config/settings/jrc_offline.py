# JR Content: přidáno 2026-10-22 (blok F2-B01), AGPL-3.0.
"""Vypnutí odchozích integrací ještě před vedlejšími účinky importu base.py."""

import os

for name in (
    "SENTRY_DSN",
    "UNSPLASH_ACCESS_KEY",
    "INTELLIGENCE_INTERNAL_URL",
    "INTELLIGENCE_PUBLIC_URL",
    "STUDIO_DEPLOYMENT_ID",
    "STUDIO_SHARED_SECRET",
    "STUDIO_BASE_URL",
    "GOOGLE_AUTH_CLIENT_ID",
    "GOOGLE_AUTH_CLIENT_SECRET",
    "REDIS_URL",
):
    os.environ[name] = ""
os.environ["EMAIL_BACKEND_TYPE"] = "console"
os.environ["STORAGE_BACKEND"] = "local"

# Zde budou přibývat aplikace z dalších bloků; stejné se načítají i v testech.
JRC_APPS = []
