"""P1-B34: every model attribute and import the code names must exist.

Run inside the Brightbean container (Django settings loaded):
    python /jrc-scripts/P1-B34/check_model_refs.py [root ...]

The upstream ``SocialAccount.Platform`` (moved to ``PlatformCredential``) only
failed when the CSV preview ran, because the import sits inside the view. This
walks every Python file (no migrations) and reports
  * ``from x import y`` (any scope) where ``y`` does not exist in ``x``;
  * ``Model.attr`` where ``Model`` is a name bound to a Django model class (by an
    import in the same file) and the class has no such attribute.
Exit status 1 when anything is reported.
"""

import ast
import importlib
import os
import pathlib
import sys

import django

sys.path.insert(0, os.getcwd())
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.jrc_test")
django.setup()

from django.db import models  # noqa: E402

roots = [pathlib.Path(p) for p in (sys.argv[1:] or ["apps", "config", "providers", "tests"])]
problems = []
checked_attrs = 0
checked_imports = 0


def resolve(module, name):
    mod = importlib.import_module(module)
    if hasattr(mod, name):
        return getattr(mod, name)
    # ``from package import submodule``
    return importlib.import_module(f"{module}.{name}")


for root in roots:
    for path in sorted(root.rglob("*.py")):
        if "migrations" in path.parts or "node_modules" in path.parts:
            continue
        tree = ast.parse(path.read_text(), str(path))
        package = ".".join(path.with_suffix("").parts[:-1])
        bound = {}  # local name -> model class
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.module is None and node.level == 0:
                continue
            if node.level:
                base = package.split(".")
                base = base[: len(base) - node.level + 1]
                module = ".".join(base + ([node.module] if node.module else []))
            else:
                module = node.module
            for alias in node.names:
                if alias.name == "*":
                    continue
                checked_imports += 1
                try:
                    obj = resolve(module, alias.name)
                except Exception as exc:  # noqa: BLE001 - report whatever fails
                    problems.append(f"{path}:{node.lineno}: from {module} import {alias.name}: {exc!r}")
                    continue
                if isinstance(obj, type) and issubclass(obj, models.Model):
                    bound[alias.asname or alias.name] = obj
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in bound:
                model = bound[node.value.id]
                checked_attrs += 1
                if not hasattr(model, node.attr):
                    problems.append(f"{path}:{node.lineno}: {model.__name__}.{node.attr} does not exist")

print(f"checked {checked_imports} imports, {checked_attrs} model attribute references")
for line in problems:
    print("PROBLEM", line)
sys.exit(1 if problems else 0)
