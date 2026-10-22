# JR Content: přidáno 2026-10-22 (blok F2-B01), AGPL-3.0.
from .jrc_offline import JRC_APPS

# isort: split
from .test import *  # noqa: F401, F403, E402

INSTALLED_APPS = [*INSTALLED_APPS, *JRC_APPS]  # noqa: F405
JRC_SOCIAL_PROVIDERS_ENABLED = False
