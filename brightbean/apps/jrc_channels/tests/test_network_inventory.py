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
        # F5-B01: internal Docker API only (jrc-ff-api:8000), no redirects/proxy.
        ("jrc_video/client.py", "FreeFrameClient._send", "requests.request"): 1,
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
            target = qualified(node.func)
            if target in {"getattr", "builtins.getattr"} and len(node.args) >= 2:
                attr = node.args[1]
                if isinstance(attr, ast.Constant) and isinstance(attr.value, str):
                    return f"{qualified(node.args[0])}.{attr.value}"
            if target in {"importlib.import_module", "__import__", "builtins.__import__"} and node.args:
                module = node.args[0]
                if isinstance(module, ast.Constant) and isinstance(module.value, str):
                    if (
                        target != "importlib.import_module"
                        and len(node.args) < 4
                        and not any(k.arg == "fromlist" for k in node.keywords)
                    ):
                        return module.value.split(".")[0]
                    return module.value
            return target
        return ast.unparse(node)

    constructors = {"httpx.Client", "httpx.AsyncClient", "requests.Session", "requests.sessions.Session"}

    def binding(value):
        name = qualified(value)
        if isinstance(value, ast.Call) and qualified(value.func) not in {
            "getattr",
            "builtins.getattr",
            "importlib.import_module",
            "__import__",
            "builtins.__import__",
        }:
            return name if name in constructors else None
        return (
            name
            if name.startswith(("httpx", "requests", "providers", "importlib", "builtins"))
            or name in {"getattr", "__import__"}
            else None
        )

    # Follow import aliases and Client/Session bindings, not HTTP responses.
    for _ in range(len(list(ast.walk(tree)))):
        previous = aliases.copy()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and (name := binding(node.value)):
                        aliases[target.id] = name
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if isinstance(item.optional_vars, ast.Name) and (name := binding(item.context_expr)):
                        aliases[item.optional_vars.id] = name
        if aliases == previous:
            break

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
        if isinstance(node, (ast.Name, ast.Attribute, ast.Call)) and qualified(node).endswith("PROVIDER_REGISTRY"):
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
        "import httpx\nc: httpx.Client = httpx.Client()\nc.get('https://example.invalid')",
        "import requests\ns: object = requests.Session()\ns.get('https://example.invalid')",
        "from providers.instagram import InstagramProvider\ncls: type = InstagramProvider\ncls()",
        "import providers.instagram as ig\ncls = getattr(ig, 'InstagramProvider')\ncls()",
        "import httpx\nfetch = getattr(httpx, 'get')\nfetch('https://example.invalid')",
        "import requests\ngetattr(requests, 'get')('https://example.invalid')",
        "import importlib\nm = importlib.import_module('httpx')\nm.get('https://example.invalid')",
        "from importlib import import_module as load\nm: object = load('requests')\nm.get('https://example.invalid')",
        "import importlib\nm = importlib.import_module('providers.instagram')\ncls = m.InstagramProvider\ncls()",
        "m = __import__('httpx')\nm.get('https://example.invalid')",
        "m = __import__('providers.instagram', fromlist=['InstagramProvider'])\ncls = m.InstagramProvider\ncls()",
        "m = __import__('providers.instagram')\nm.instagram.InstagramProvider()",
        "import builtins as b\nload = b.__import__\nm = load('requests')\nm.get('https://example.invalid')",
        "from builtins import getattr as attr\nimport httpx\nf: object = attr(httpx, 'get')\nf('https://example.invalid')",
        "import providers\nr = getattr(providers, 'PROVIDER_REGISTRY')\nr['instagram']()",
        "from providers.instagram import InstagramProvider\na = InstagramProvider\nb = a\nc = b\nd = c\ne = d\ne()",
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


@pytest.mark.parametrize(
    "source,method",
    [
        ("import httpx\nc: object = httpx.Client()\nc.get('https://example.invalid')", "httpx.Client.get"),
        ("import requests\ns: object = requests.Session()\ns.post('https://example.invalid')", "requests.Session.post"),
    ],
)
def test_annotated_clients_track_requests_as_well_as_construction(source, method):
    calls, violations, _ = inspect_source(source, "new_module.py")
    assert not violations
    assert calls["new_module.py", "", method] == 1
