# JR Content: přidáno 2026-11-07 (blok P1-B36), upraveno 2026-11-07 (blok P1-B37), AGPL-3.0.
"""Guard: every code path that edits reviewed content goes through ``apps.approvals.edits`` (P-025).

Reviewed content is what goes out: a post's title, caption, first comment, tags and media, and each
channel copy's own title, caption and first comment, its final video version and its thumbnail
(P1-B37). The test reads the whole application code (``apps/``, no tests or migrations) and finds
every function that writes one of them: an assignment ``x.caption = …`` / ``video.final_version = …``,
``setattr(x, "caption", …)``, a queryset ``.update(caption=…)``, every create, change or delete of
``PostMedia`` rows and every new ``ChannelThumbnail``. Each such function must either take ``reviewed_content`` and
call ``return_edited_to_review`` itself, or be listed below with the reason it does not have to
(it creates new content, or it is a helper whose only callers do it). A ``PostForm`` bound to
submitted data counts as a write too (the composer saves the base texts through it). A new edit
path therefore fails here until it is routed through the rule or consciously listed; an exempted
helper must be called only from functions that are routed.
"""

import ast
import pathlib

import apps

APPS = pathlib.Path(apps.__file__).resolve().parent

# Fields of Post / PlatformPost that are reviewed content. ``title`` and ``tags`` are common names
# (ideas, media, events), so they count only on a receiver named like a post.
DISTINCT_FIELDS = {
    "caption",
    "first_comment",
    "platform_specific_title",
    "platform_specific_caption",
    "platform_specific_first_comment",
    "final_version",
}
POST_ONLY_FIELDS = {"title", "tags"}
POST_RECEIVERS = {"post", "new_post", "clone", "pp", "variant", "copy", "child", "platform_post"}
MEDIA_WRITES = {"create", "get_or_create", "update_or_create", "bulk_create", "bulk_update", "update", "delete"}

# function (module:qualified name) -> why it does not call the rule itself.
NOT_AN_EDIT = {
    # New content: nothing was approved yet.
    "composer/services.py:create_post": "creates new content",
    "composer/services.py:clone_post": "creates a copy of the content as new content",
    "composer/views.py:idea_create_post": "creates new content from an idea",
    "composer/views.py:_import_csv_row": "creates new content from a CSV row",
    "calendar/tasks.py:generate_recurring_posts": "creates the next post of a recurring series",
    # Helpers whose only callers take the snapshot and call the rule.
    "composer/views.py:_sync_platform_posts": "called by save_post, which calls the rule after it",
    "composer/views.py:_attach_asset_for_composer": "called by upload_media, which calls the rule after it",
    "jrc_texts/services.py:_save_caption_version": "called by save_caption, which calls the rule after it",
}


def _is_post_like(node):
    name = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else ""
    return name in POST_RECEIVERS


def _writes_field(node):
    """Does this AST node write a reviewed field? Returns a short description or ""."""
    if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            for item in target.elts if isinstance(target, ast.Tuple) else [target]:
                if isinstance(item, ast.Attribute) and (
                    item.attr in DISTINCT_FIELDS or (item.attr in POST_ONLY_FIELDS and _is_post_like(item.value))
                ):
                    return f"assigns .{item.attr}"
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id.endswith("PostForm") and node.args:
            return "PostForm bound to submitted data"
        if isinstance(func, ast.Name) and func.id == "ChannelThumbnail":
            return "new ChannelThumbnail"
        if isinstance(func, ast.Name) and func.id == "setattr" and len(node.args) >= 2:
            field = node.args[1]
            if isinstance(field, ast.Constant) and field.value in DISTINCT_FIELDS:
                return f"setattr {field.value}"
        if isinstance(func, ast.Attribute):
            if func.attr == "update" and any(kw.arg in DISTINCT_FIELDS for kw in node.keywords):
                return "queryset update of a reviewed field"
            if func.attr in MEDIA_WRITES and "PostMedia" in ast.unparse(func.value):
                return f"PostMedia {func.attr}"
            if func.attr in MEDIA_WRITES and "media_attachments" in ast.unparse(func.value):
                return f"media_attachments {func.attr}"
            if func.attr in MEDIA_WRITES and "ChannelThumbnail" in ast.unparse(func.value):
                return f"ChannelThumbnail {func.attr}"
    return ""


def _functions(tree):
    """Yield (qualified name, node) of every function, methods as Class.method."""

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                yield name, child
                yield from walk(child, f"{name}.")
            elif isinstance(child, ast.ClassDef):
                yield from walk(child, f"{prefix}{child.name}.")

    yield from walk(tree, "")


def _own_nodes(function):
    """Nodes of ``function`` without the bodies of functions nested in it."""
    stack = list(function.body)
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                stack.append(child)


def _calls(function, name):
    return any(
        isinstance(node, ast.Call)
        and (getattr(node.func, "id", None) == name or getattr(node.func, "attr", None) == name)
        for node in _own_nodes(function)
    )


def _source_files():
    for path in sorted(APPS.rglob("*.py")):
        rel = path.relative_to(APPS).as_posix()
        parts = rel.split("/")
        if "migrations" in parts or "tests" in parts or parts[-1].startswith("test") or parts[-1] == "conftest.py":
            continue
        yield rel, ast.parse(path.read_text(encoding="utf-8"))


def find_writers():
    """``{"module:function": [what it writes, …]}`` for every function writing reviewed content."""
    writers = {}
    for rel, tree in _source_files():
        for name, function in _functions(tree):
            found = sorted({hit for node in _own_nodes(function) if (hit := _writes_field(node))})
            if found:
                writers[f"{rel}:{name}"] = found
    return writers


def test_every_writer_of_reviewed_content_goes_through_the_rule():
    trees = dict(_source_files())
    unrouted = []
    for key, found in find_writers().items():
        if key in NOT_AN_EDIT:
            continue
        rel, name = key.split(":", 1)
        function = dict(_functions(trees[rel]))[name]
        if not (_calls(function, "return_edited_to_review") and _calls(function, "reviewed_content")):
            unrouted.append(f"{key} ({', '.join(found)})")
    assert not unrouted, (
        "These functions change reviewed content without apps.approvals.edits (take reviewed_content() "
        "before the change and call return_edited_to_review() after it), or list them in NOT_AN_EDIT "
        "with the reason:\n" + "\n".join(unrouted)
    )


def test_the_exemption_list_names_real_writers():
    """An exemption that no longer writes anything (renamed, removed) must not linger."""
    writers = find_writers()
    stale = [key for key in NOT_AN_EDIT if key not in writers]
    assert not stale, f"Remove or fix these NOT_AN_EDIT entries: {stale}"


def test_the_guard_sees_the_known_edit_paths():
    """The scan itself works: the known edit paths are found as writers (so a passing guard means something)."""
    writers = find_writers()
    for key in (
        "composer/views.py:save_post",
        "composer/views.py:remove_media",
        "composer/views.py:attach_media",
        "api/routers/posts.py:update",
        "jrc_texts/services.py:_save_caption_version",
        "jrc_video/player.py:set_final",
        "jrc_thumbnails/services.py:upload_thumbnail",
    ):
        assert key in writers, key


def test_exempt_helpers_are_only_called_by_routed_edits():
    """A helper exempted because "its callers call the rule" really is called only from such callers."""
    helpers = {key.split(":", 1)[1] for key, reason in NOT_AN_EDIT.items() if reason.startswith("called by")}
    trees = dict(_source_files())
    callers = {helper: [] for helper in helpers}
    for rel, tree in trees.items():
        for name, function in _functions(tree):
            for helper in helpers:
                if _calls(function, helper):
                    callers[helper].append((f"{rel}:{name}", function))
    for helper, found in callers.items():
        assert found, f"{helper} is not called anywhere; drop its exemption"
        for key, function in found:
            assert _calls(function, "return_edited_to_review") and _calls(function, "reviewed_content"), (
                f"{key} calls {helper} but does not go through apps.approvals.edits"
            )
