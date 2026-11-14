"""Run channel and write-access regression tests in their own test database."""
import argparse
import json
import os
import sys
from pathlib import Path

assert os.environ["JRC_PROJECT"] == "jrc-p2b12"
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings.jrc_test"
os.environ["NODE_OPTIONS"] = "--max-old-space-size=2048"
os.environ["PGOPTIONS"] = "-c synchronous_commit=off"
sys.path.insert(0, str(Path.cwd()))

import django
import pytest
from django.conf import settings

django.setup()
settings.DATABASES["default"].setdefault("TEST", {})["NAME"] = "test_p2b12_targeted"
parser = argparse.ArgumentParser()
parser.add_argument("--batch", type=int)
parser.add_argument("--part", type=int, choices=[1, 2])
options = parser.parse_args()
paths = ["apps/jrc_channels/tests", "apps/jrc_ui/tests/test_write_access.py", "apps/jrc_ui/tests/test_write_access_review.py"]
if options.batch:
    paths = json.loads(Path("output/p2b12-bb/manifest.json").read_text())[options.batch - 1]["paths"]
    if options.part:
        middle = len(paths) // 2
        paths = paths[:middle] if options.part == 1 else paths[middle:]
database_options = ["--reuse-db"] + (["--create-db"] if options.batch in {None, 1} else [])
raise SystemExit(pytest.main([
    "-p", "no:cacheprovider", "-o", "addopts=", "--ds=config.settings.jrc_test",
    *database_options, "-q", "-x", "--tb=short",
    *paths,
]))
