"""Celá sada BB po menších dávkách; pouze uvnitř vlastního Docker projektu."""
import json
import os
import sys
from collections import Counter
from pathlib import Path

import pytest

assert os.environ['JRC_PROJECT'] == 'jrc-p2b20'
plan = Path('/tmp/p2b20-batches.json')
common = ['--reuse-db', '--create-db', '--maxfail=10', '--durations=5', '-p', 'no:cacheprovider', '-o', 'addopts=', '-q', '--tb=short', '--ds=' + os.environ.get('P2B20_TEST_SETTINGS', 'config.settings.jrc_test')]


class Batches:
    def pytest_collection_finish(self, session):
        counts = Counter(item.nodeid.split('::')[0] for item in session.items)
        groups = [[] for _ in range(12)]
        weights = [0] * 12
        for file, count in counts.most_common():
            group = weights.index(min(weights))
            groups[group].append(file)
            weights[group] += count
        plan.write_text(json.dumps({'files': groups, 'counts': weights}))
        print('Plán celé sady BB:', weights, 'testů')


if sys.argv[1] == 'collect':
    raise SystemExit(pytest.main([*common, '--collect-only'], plugins=[Batches()]))
# Transactional tests flush migration seed rows too, even after a successful batch.
# Recreate the database for every batch so file grouping cannot remove those rows.
raise SystemExit(pytest.main([*common, *json.loads(plan.read_text())['files'][int(sys.argv[1])]]))
