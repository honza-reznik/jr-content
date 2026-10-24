"""Úklid výhradně nového adresáře konkrétního integračního testu, v Dockeru."""
import shutil
import sys
from pathlib import Path
from jrc_backup import safe_path

root = safe_path(Path.cwd() / 'backups')
target = safe_path(sys.argv[1], root)
if target.parent != root or '-f2b09-' not in target.name:
    raise SystemExit('Neplatný vlastní testovací adresář.')
for path in target.rglob('*'):
    safe_path(path, root)
shutil.rmtree(target)
