"""Run one part of the full collected BB suite, only inside our own container."""
import json
import os
import sys
from pathlib import Path
import pytest
assert os.environ['P2B05_TEST_DATABASE'].startswith('test_brightbean_p2b05_batch')
files = json.loads(Path('/tmp/p2b05-batches.json').read_text())[int(sys.argv[1])]
raise SystemExit(pytest.main(['-p', 'no:cacheprovider', '-o', 'addopts=', '-q', '--tb=short', '--ds=p2b05_test', *files]))
