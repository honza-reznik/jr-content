"""Pack whole test files into shard manifests of at most LIMIT tests (P1-B36).

Usage: python make-shards.py <collect-output> <outdir> [path-prefix ...]
"""
import collections
import pathlib
import sys

LIMIT = 300
source, outdir, *prefixes = sys.argv[1:]
counts = collections.OrderedDict()
for line in pathlib.Path(source).read_text().splitlines():
    if "::" not in line:
        continue
    path = line.split("::", 1)[0]
    if prefixes and not any(path.startswith(p) for p in prefixes):
        continue
    counts[path] = counts.get(path, 0) + 1
out = pathlib.Path(outdir)
out.mkdir(parents=True, exist_ok=True)
for old in out.glob("shard-*.txt"):
    old.unlink()
shards, current, size = [], [], 0
for path, n in counts.items():
    if current and size + n > LIMIT:
        shards.append(current)
        current, size = [], 0
    current.append(path)
    size += n
if current:
    shards.append(current)
for i, files in enumerate(shards):
    (out / f"shard-{i:02d}.txt").write_text("\n".join(files) + "\n")
print(len(shards), "shards,", sum(counts.values()), "tests,", len(counts), "files")
