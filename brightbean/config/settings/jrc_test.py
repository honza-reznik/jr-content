# JR Content: přidáno 2026-10-22 (blok F2-B01), AGPL-3.0.
from .jrc_offline import JRC_APPS

# isort: split
from .test import *  # noqa: F401, F403, E402

INSTALLED_APPS = [*INSTALLED_APPS, *JRC_APPS]  # noqa: F405
JRC_SOCIAL_PROVIDERS_ENABLED = False

# JR Content: upraveno 2026-10-23 (blok F2-B10), AGPL-3.0.
from apps.jrc_ui.features import configure_ui  # noqa: E402

configure_ui(globals())
# Upstream regresní sada ověřuje i skryté funkce; naše testy zapínají MVP_FEATURES.
JRC_FEATURES = {}
