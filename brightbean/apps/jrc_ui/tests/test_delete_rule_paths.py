# JR Content: přidáno 2026-11-07 (blok P1-B37), AGPL-3.0.
"""Guard: every path that deletes content or channel copies applies the one deletion rule (P1-B37).

The rule is ``apps.members.permissions.content_delete_blocker``: deleting needs the right to edit the
content, and deleting a planned copy also the right to take it out of the plan (``can_unschedule``).
The test reads the whole application code (``apps/``, no tests or migrations) and finds every
function that deletes a post or channel copy: a call of ``remove_platform_posts`` (the composer and
calendar helper) or ``.delete()`` on a receiver named like a post or copy. Each such function must
call ``content_delete_blocker`` itself, or be listed below with the reason it does not have to.
"""

import ast

from apps.jrc_ui.tests.test_reviewed_content_writers import _calls, _functions, _own_nodes, _source_files

DELETE_RECEIVERS = {"post", "pp", "variant", "platform_post", "child", "copy"}

# function (module:qualified name) -> why it does not apply the rule itself.
NOT_A_MEMBER_DELETION = {
    "composer/services.py:remove_platform_posts": "the shared helper; its callers apply the rule",
    "composer/views.py:_remove_deselected_platform_posts": (
        "called by save_post and autosave after _caption_selection_error applied the rule"
    ),
    "social_accounts/youtube_compliance.py:check_connection": (
        "YouTube API compliance: removes published copies whose video is gone from YouTube"
    ),
}


def _deletes(function):
    for node in _own_nodes(function):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if getattr(func, "id", None) == "remove_platform_posts" or getattr(func, "attr", None) == (
            "remove_platform_posts"
        ):
            return True
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "delete"
            and isinstance(func.value, ast.Name)
            and func.value.id in DELETE_RECEIVERS
        ):
            return True
    return False


def find_deleters():
    found = {}
    for rel, tree in _source_files():
        for name, function in _functions(tree):
            if _deletes(function):
                found[f"{rel}:{name}"] = function
    return found


def test_every_deletion_path_applies_the_rule():
    unrouted = [
        key
        for key, function in find_deleters().items()
        if key not in NOT_A_MEMBER_DELETION and not _calls(function, "content_delete_blocker")
    ]
    assert not unrouted, (
        "These functions delete content or channel copies without content_delete_blocker "
        "(apps.members.permissions), or list them in NOT_A_MEMBER_DELETION with the reason:\n" + "\n".join(unrouted)
    )


def test_the_exemption_list_names_real_deleters():
    deleters = find_deleters()
    stale = [key for key in NOT_A_MEMBER_DELETION if key not in deleters]
    assert not stale, f"Remove or fix these NOT_A_MEMBER_DELETION entries: {stale}"


def test_the_guard_sees_the_known_deletion_paths():
    deleters = find_deleters()
    for key in (
        "composer/views.py:post_delete",
        "calendar/views.py:bulk_platform_action",
        "jrc_production/services.py:remove_content_channel",
    ):
        assert key in deleters, key


def test_deselection_helper_is_only_called_after_the_rule():
    """Deselected channels are deleted only by saves that checked the selection with the rule first."""
    trees = dict(_source_files())

    def callers(name):
        return [
            (f"{rel}:{fname}", function)
            for rel, tree in trees.items()
            for fname, function in _functions(tree)
            if _calls(function, name)
        ]

    selection = dict(_functions(trees["composer/views.py"]))["_caption_selection_error"]
    assert _calls(selection, "content_delete_blocker")
    direct = callers("_remove_deselected_platform_posts")
    assert direct
    for key, function in direct:
        if key == "composer/views.py:_sync_platform_posts":
            sync_callers = callers("_sync_platform_posts")
            assert sync_callers
            for sync_key, sync_function in sync_callers:
                assert _calls(sync_function, "_caption_selection_error"), sync_key
        else:
            assert _calls(function, "_caption_selection_error"), key
