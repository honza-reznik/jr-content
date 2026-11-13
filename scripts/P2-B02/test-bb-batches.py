"""Celá BB sada po dávkách; spouštět pouze uvnitř vlastního Docker projektu."""
import collections
import os
import subprocess

assert os.environ.get("JRC_PROJECT") == "jrc-p2b02"
os.environ["NODE_OPTIONS"] = "--max-old-space-size=2048"
base = ["pytest", "-p", "no:cacheprovider", "-o", "addopts=", "--ds=config.settings.jrc_test"]
collected = subprocess.run(base + ["--collect-only", "-q"], capture_output=True, text=True, timeout=120, check=True)
files = collections.Counter(line.split("::", 1)[0] for line in collected.stdout.splitlines() if "::" in line and ".py::" in line)
assert sum(files.values()) > 5500, "Neúplný sběr testů"
groups = []
current, count = [], 0
# Nejprve vlastní moduly JR Content, poté všechny zbývající testy.
for file in sorted(files, key=lambda file: ("/jrc_" not in file, file)):
    if current and count + files[file] > 900:
        groups.append((current, count))
        current, count = [], 0
    current.append(file)
    count += files[file]
if current:
    groups.append((current, count))
print(f"Celkem {sum(files.values())} testů v {len(groups)} dávkách.", flush=True)
failed = False
for number, (paths, count) in enumerate(groups, 1):
    print(f"Dávka {number}/{len(groups)}: {count} testů, {paths[0]} až {paths[-1]}", flush=True)
    result = subprocess.run(base + ["--reuse-db", "-q", "--tb=short", "--durations=5", *paths], timeout=1800)
    failed |= bool(result.returncode)
    print(f"Dávka {number}: návratový kód {result.returncode}", flush=True)
raise SystemExit(1 if failed else 0)
