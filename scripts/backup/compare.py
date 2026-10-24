"""Porovnání stavu zdrojového a obnoveného projektu (F2-B09). Argumenty: před, po, obnovený, seed-ff, média-zdroj, média-obnova."""
import json
import sys
from pathlib import Path

before, after, restored = (json.loads(Path(p).read_text()) for p in sys.argv[1:4])
seed_ff = json.loads(Path(sys.argv[4]).read_text())
media_src, media_dst = Path(sys.argv[5]).read_text(), Path(sys.argv[6]).read_text()
problems = []
rows = 0
volatile = []
growing = []
GROWING = {"public.background_task", "public.background_task_completedtask"}
for database, tables in before["tables"].items():
    assert tables, f"databáze {database} nemá žádné tabulky"
    for table, count in tables.items():
        rows += count
        end = after["tables"][database].get(table)
        got = restored["tables"][database].get(table)
        if got is None:
            problems.append(f"{database}.{table}: tabulka po obnově chybí")
        elif table in GROWING:
            # Fronta a protokol úloh na pozadí: po obnově hned pracuje nový worker, řádků smí přibýt, nikdy ubýt.
            if got < min(count, end):
                problems.append(f"{database}.{table}: {got} řádků po obnově je méně než ve zdroji ({count})")
            elif got != count:
                growing.append(f"{database}.{table} ({count}->{got})")
        elif count == end:
            if got != count:
                problems.append(f"{database}.{table}: {count} řádků ve zdroji, {got} po obnově")
        else:
            volatile.append(f"{database}.{table} ({count}->{end}, obnoveno {got})")
            if not min(count, end) <= got <= max(count, end):
                problems.append(f"{database}.{table}: obnovených {got} řádků je mimo rozsah {count}..{end}")
    extra = set(restored["tables"][database]) - set(tables)
    if extra:
        problems.append(f"{database}: tabulky navíc po obnově: {sorted(extra)}")
if before["objects"] != after["objects"]:
    problems.append("objekty S3 ve zdroji se během zálohy změnily")
if restored["objects"] != before["objects"]:
    for bucket in sorted(set(before["objects"]) | set(restored["objects"])):
        if before["objects"].get(bucket) != restored["objects"].get(bucket):
            problems.append(f"bucket {bucket}: objekty (klíč, velikost, SHA-256) se po obnově liší")
objects = sum(len(v) for v in before["objects"].values())
if objects < 5:
    problems.append(f"zdroj má jen {objects} objektů S3; seed nefungoval")
video_hashes = {sha for _, sha in restored["objects"]["jrc-video"].values()}
if seed_ff["source_sha256"] not in video_hashes:
    problems.append("originál testovacího videa v obnoveném jrc-video chybí nebo má jiný součet")
if media_src != media_dst or "stara-media.txt" not in media_dst:
    problems.append("svazek jrc-bb-media se po obnově liší")
if problems:
    print("\n".join("ROZDÍL: " + p for p in problems))
    sys.exit(1)
print(f"OK porovnání: {rows} řádků v {sum(len(t) for t in before['tables'].values())} tabulkách, "
      f"{objects} objektů S3 se shodnými součty, svazek médií shodný"
      + (f"; proměnlivé tabulky v rozsahu: {', '.join(volatile)}" if volatile else "")
      + (f"; fronta úloh po obnově přibyla: {', '.join(growing)}" if growing else ""))
