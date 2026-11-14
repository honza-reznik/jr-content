"""Collect the entire BB suite, or run one bounded batch, only in jrc-p2b12."""
import collections
import json
import os
import subprocess
import sys
from pathlib import Path

assert os.environ["JRC_PROJECT"] == "jrc-p2b12"
os.environ["NODE_OPTIONS"] = "--max-old-space-size=2048"
output = Path("output/p2b12-bb")
output.mkdir(exist_ok=True)
base = ["pytest", "-p", "no:cacheprovider", "-o", "addopts=", "--ds=config.settings.jrc_test"]
if sys.argv[1:] in (["collect"], ["all"]):
    result = subprocess.run(base + ["--collect-only", "-q"], capture_output=True, text=True, timeout=120, check=True)
    files = collections.Counter(line.split("::", 1)[0] for line in result.stdout.splitlines() if ".py::" in line)
    assert sum(files.values()) > 5500
    groups = []
    paths, count = [], 0
    for path in sorted(files, key=lambda name: ("/jrc_" not in name, name)):
        if paths and count + files[path] > 900:
            groups.append({"paths": paths, "count": count})
            paths, count = [], 0
        paths.append(path)
        count += files[path]
    if paths:
        groups.append({"paths": paths, "count": count})
    (output / "manifest.json").write_text(json.dumps(groups))
    print(f"Collected {sum(files.values())} tests in {len(groups)} batches.", flush=True)
    if sys.argv[1:] == ["all"]:
        failed = False
        for number in range(1, len(groups) + 1):
            result = subprocess.run([sys.executable, __file__, str(number)], timeout=590)
            failed |= bool(result.returncode)
        raise SystemExit(1 if failed else 0)
else:
    number = int(sys.argv[1])
    group = json.loads((output / "manifest.json").read_text())[number - 1]
    print(f"Batch {number}: {group['count']} tests", flush=True)
    with (output / f"batch-{number}.log").open("w") as log:
        result = subprocess.run(base + ["--reuse-db", "-q", "--tb=short", *group["paths"]], stdout=log, stderr=subprocess.STDOUT, timeout=570)
    print((output / f"batch-{number}.log").read_text().splitlines()[-1], flush=True)
    raise SystemExit(result.returncode)
