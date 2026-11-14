"""Run the entire Brightbean collection sequentially in bounded batches inside Docker."""

import os
from pathlib import Path
import re
import subprocess
import sys

assert os.environ.get("JRC_PROJECT") == "jrc-p2b09"
output = Path("/app/output/P2-B09/full-bb")
output.mkdir(parents=True, exist_ok=True)
command = [
    sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-o", "addopts=",
    "-q", "--tb=short", "--ds=config.settings.jrc_test",
]
rerun = int(sys.argv[2]) if len(sys.argv) == 3 and sys.argv[1] == "--rerun" else None
if rerun is not None:
    collected = (output / "collection.log").read_text()
else:
    collection = subprocess.run(command + ["--collect-only"], capture_output=True, text=True, timeout=120)
    collected = collection.stdout + collection.stderr
    (output / "collection.log").write_text(collected)
    if collection.returncode:
        raise SystemExit(collection.returncode)
items = [line for line in collected.splitlines() if line.startswith(("apps/", "tests/")) and "::" in line]
assert items and len(items) == len(set(items))
print(f"Collected {len(items)} tests; batches of 500, each limited to 10 minutes.", flush=True)
failures = []
for start in range(0, len(items), 500):
    number = start // 500 + 1
    if rerun is not None and number != rerun:
        continue
    batch = items[start:start + 500]
    # Some existing tests generate a fresh UUID parameter during collection. Select that
    # whole function, including its stable cases, instead of carrying an obsolete UUID.
    dynamic = {item.split("[")[0] for item in batch if re.search(r"\[[0-9a-f]{8}-[0-9a-f-]{27}\]$", item)}
    selectors = list(dict.fromkeys(item.split("[")[0] if item.split("[")[0] in dynamic else item for item in batch))
    prefix = "rerun-" if rerun is not None else ""
    with (output / f"{prefix}batch-{number:02}.log").open("w") as log:
        try:
            result = subprocess.run(command + selectors, stdout=log, stderr=subprocess.STDOUT, timeout=600)
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = 124
    if code:
        failures.append(number)
    print(f"Batch {number}: exit {code}; {min(start + 500, len(items))}/{len(items)} tests attempted.", flush=True)
print(f"Failed batches: {failures}", flush=True)
raise SystemExit(bool(failures))
