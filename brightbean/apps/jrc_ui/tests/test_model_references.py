# JR Content: přidáno 2026-11-07 (blok P1-B34), AGPL-3.0.
"""Every model attribute and import the code names exists (P1-B34).

Upstream moved the platform choices from ``SocialAccount.Platform`` to ``PlatformCredential.Platform``
and left one reference behind in the CSV preview. Because the import sits inside the view and Python
resolves class attributes only when the line runs, nothing failed until a user reached that step
(500). This test resolves, without running the code:

* every ``from x import y`` in the application, also inside functions;
* every ``Model.attr`` where ``Model`` is a Django model class imported in the same file.
"""

import ast
import importlib
import pathlib

from django.db import models

ROOT = pathlib.Path(__file__).resolve().parents[3]
SOURCES = ("apps", "providers")


def _resolve(module, name):
    imported = importlib.import_module(module)
    if hasattr(imported, name):
        return getattr(imported, name)
    return importlib.import_module(f"{module}.{name}")  # ``from package import submodule``


def _problems():
    problems, attributes = [], 0
    for source in SOURCES:
        for path in sorted((ROOT / source).rglob("*.py")):
            if "migrations" in path.parts:
                continue
            relative = path.relative_to(ROOT)
            tree = ast.parse(path.read_text(encoding="utf-8"), str(relative))
            package = relative.with_suffix("").parts[:-1]
            bound = {}
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                if node.level:
                    base = list(package[: len(package) - node.level + 1])
                    module = ".".join(base + ([node.module] if node.module else []))
                else:
                    module = node.module
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    try:
                        target = _resolve(module, alias.name)
                    except Exception as exc:  # noqa: BLE001 - report whatever cannot be resolved
                        problems.append(f"{relative}:{node.lineno}: from {module} import {alias.name}: {exc!r}")
                        continue
                    if isinstance(target, type) and issubclass(target, models.Model):
                        bound[alias.asname or alias.name] = target
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in bound:
                    attributes += 1
                    model = bound[node.value.id]
                    if not hasattr(model, node.attr):
                        problems.append(f"{relative}:{node.lineno}: {model.__name__}.{node.attr} does not exist")
    return problems, attributes


def test_every_import_and_model_attribute_in_the_code_exists():
    problems, attributes = _problems()
    assert attributes > 1000, "the walk found the model references"
    assert problems == []
