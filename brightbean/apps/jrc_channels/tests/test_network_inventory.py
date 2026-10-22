"""AST audit of upstream network entry points, as required by ADR-002.

The allowlist counts individual calls by file, enclosing function and resolved
import name. Moving/adding a call requires reviewing this inventory explicitly;
line-number changes alone do not invalidate it. Provider code itself is gated
by get_provider and is outside apps/.
"""

import ast
from collections import Counter
from pathlib import Path

import pytest

APPS = Path(__file__).resolve().parents[2]

# Existing non-social HTTP integrations, documented in ADR-002. Constructors
# AND calls on their client instances are listed, so another request inside an
# otherwise allowed function is also caught.
ALLOWED_HTTP = Counter(
    {
        ("composer/views.py", "unsplash_search", "httpx.get"): 1,
        ("composer/views.py", "unsplash_import", "httpx.Client"): 1,
        ("composer/views.py", "unsplash_import", "httpx.Client.get"): 1,
        ("composer/views.py", "unsplash_import", "httpx.Client.stream"): 1,
        ("composer/views.py", "_safe_fetch_feed", "httpx.get"): 2,
        ("notifications/engine.py", "_dispatch_webhook", "httpx.post"): 1,
        ("intelligence/services/client.py", "InternalClient._request", "httpx.Client"): 1,
        ("intelligence/services/client.py", "InternalClient._request", "httpx.Client.request"): 1,
        ("intelligence/services/client.py", "IntelligenceAPIClient._request", "httpx.Client"): 1,
        ("intelligence/services/client.py", "IntelligenceAPIClient._request", "httpx.Client.request"): 2,
    }
)


def inspect_source(source, filename):
    tree = ast.parse(source)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"

    def qualified(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return f"{qualified(node.value)}.{node.attr}"
        if isinstance(node, ast.Call):
            return qualified(node.func)
        return ast.unparse(node)

    constructors = {"httpx.Client", "httpx.AsyncClient", "requests.Session", "requests.sessions.Session"}

    def binding(value):
        name = qualified(value)
        if isinstance(value, ast.Call):
            return name if name in constructors else None
        return name if name.startswith(("httpx", "requests", "providers")) else None

    # Follow import aliases and Client/Session bindings, not HTTP responses.
    for _ in range(3):
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and (name := binding(node.value)):
                        aliases[target.id] = name
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if isinstance(item.optional_vars, ast.Name) and (name := binding(item.context_expr)):
                        aliases[item.optional_vars.id] = name

    def scope(node):
        names = []
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                names.append(node.name)
        return ".".join(reversed(names))

    http = Counter()
    violations = []
    metadata_reads = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.Name, ast.Attribute)) and qualified(node).endswith("PROVIDER_REGISTRY"):
            parent = parents.get(node)
            # The single exception only iterates classes to read auth_type.
            if not (
                filename == "social_accounts/views.py"
                and scope(node) == "_get_configured_platforms"
                and isinstance(parent, ast.Attribute)
                and parent.attr == "items"
            ):
                violations.append(f"{filename}:{node.lineno}: provider registry bypass")
        if not isinstance(node, ast.Call):
            continue
        target = qualified(node.func)
        if target.startswith(("httpx.", "requests.")):
            http[filename, scope(node), target] += 1
        if (
            target.startswith("providers.") and target.rsplit(".", 1)[-1].endswith("Provider")
        ) or target == "provider_cls":
            parent = parents.get(node)
            if (
                target == "provider_cls"
                and filename == "social_accounts/views.py"
                and scope(node) == "_get_configured_platforms"
                and isinstance(parent, ast.Attribute)
                and parent.attr == "auth_type"
            ):
                metadata_reads += 1
            else:
                violations.append(f"{filename}:{node.lineno}: direct provider construction: {target}")
    return http, violations, metadata_reads


def test_network_entrypoint_inventory():
    http = Counter()
    violations = []
    metadata_reads = 0
    files = 0
    for path in sorted(APPS.rglob("*.py")):
        relative = path.relative_to(APPS)
        if (
            "tests" in relative.parts
            or "migrations" in relative.parts
            or path.name.startswith("test")
            or path.name == "conftest.py"
        ):
            continue
        calls, errors, reads = inspect_source(path.read_text(), relative.as_posix())
        files += 1
        http.update(calls)
        violations.extend(errors)
        metadata_reads += reads
    assert files > 100, "The audit must inspect all applications"
    assert not violations, "\n".join(violations)
    assert http == ALLOWED_HTTP, f"New HTTP calls: {http - ALLOWED_HTTP}; removed calls: {ALLOWED_HTTP - http}"
    assert metadata_reads == 1, "Review the metadata-only provider constructor exception"


@pytest.mark.parametrize(
    "source",
    [
        "from providers.instagram import InstagramProvider\nInstagramProvider()",
        "from providers.instagram import InstagramProvider as Client\nClient()",
        "import providers.instagram as ig\nig.InstagramProvider()",
        "from providers import PROVIDER_REGISTRY\nPROVIDER_REGISTRY['instagram']()",
        "from providers import PROVIDER_REGISTRY as registry\nregistry.get('instagram')()",
        "from providers.instagram import InstagramProvider\nf = InstagramProvider\nf()",
        "provider_cls()",
        "import httpx\nhttpx.get('https://example.invalid')",
        "from httpx import Client as C\nc = C()\nc.get('https://example.invalid')",
        "from requests import get as fetch\nfetch('https://example.invalid')",
        "import requests as r\ns = r.Session()\ns.post('https://example.invalid')",
    ],
)
def test_inventory_detects_new_bypasses(source):
    calls, violations, _ = inspect_source(source, "new_module.py")
    assert violations or calls - ALLOWED_HTTP


def test_inventory_allows_guarded_factory():
    calls, violations, _ = inspect_source(
        "from providers import get_provider\nget_provider('instagram', {})", "safe.py"
    )
    assert not calls
    assert not violations
