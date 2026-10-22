# JR Content: přidáno 2026-10-22 (blok F2-B01), AGPL-3.0.
"""Kontrola pořadí importů: přepis hodnot po importu base už Sentry nezastaví."""

import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("module", ["config.settings.jrc", "config.settings.jrc_test"])
def test_external_integrations_stay_disabled(module):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
from unittest.mock import patch
with patch('sentry_sdk.init') as sentry_init:
    settings = importlib.import_module(__import__('sys').argv[1])
    sentry_init.assert_not_called()
assert not settings.SENTRY_DSN
assert not settings.UNSPLASH_ACCESS_KEY
assert not settings.INTELLIGENCE_ENABLED
assert not settings.GOOGLE_AUTH_CLIENT_ID
assert not settings.REDIS_URL
assert settings.EMAIL_BACKEND_TYPE == 'console'
assert not settings.JRC_SOCIAL_PROVIDERS_ENABLED
assert all(app in settings.INSTALLED_APPS for app in settings.JRC_APPS)
""",
            module,
        ],
        env={
            **os.environ,
            "SENTRY_DSN": "https://example@example.invalid/1",
            "UNSPLASH_ACCESS_KEY": "dummy",
            "INTELLIGENCE_INTERNAL_URL": "https://example.invalid",
            "INTELLIGENCE_PUBLIC_URL": "https://example.invalid",
            "STUDIO_DEPLOYMENT_ID": "dummy",
            "STUDIO_SHARED_SECRET": "dummy",
            "STUDIO_BASE_URL": "https://example.invalid",
            "GOOGLE_AUTH_CLIENT_ID": "dummy",
            "REDIS_URL": "redis://example.invalid",
            "EMAIL_BACKEND_TYPE": "smtp",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
