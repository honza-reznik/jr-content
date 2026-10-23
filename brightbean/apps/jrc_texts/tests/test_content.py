import pytest
from django.core.exceptions import ValidationError

from apps.jrc_texts.content import content_to_markdown, markdown_to_content, normalize_content


@pytest.mark.parametrize(
    "markdown",
    [
        "# Nadpis\n\n## Druhý\n\n### Třetí\n\nOdstavec s **tučným** a *kurzívou*.",
        "- První\n  - Vnořená odrážka\n- Druhá",
        "5. Pátý\n6. Šestý",
        "- [ ] Natočit\n- [x] Hotovo\n  - [ ] Vnořený úkol",
        "> Citace\n\n`kód`\n\n```python\nprint('ahoj')\n```",
        "Diakritika čřž a emoji 👩‍💻\n\nDruhý odstavec.",
        "Doslovné znaky: \\*hvězdy\\*, \\[závorky\\] a \\#.",
        "",
    ],
)
def test_markdown_editor_roundtrip(markdown):
    content = markdown_to_content(markdown)
    assert content["type"] == "doc"
    text = content_to_markdown(content)
    assert markdown_to_content(text) == content


def test_numbered_list_start_and_task_checked_are_preserved():
    numbered = markdown_to_content("5. Fifth\n6. Sixth")
    assert numbered["content"][0]["attrs"]["start"] == 5
    tasks = markdown_to_content("- [ ] No\n- [x] Yes")
    items = tasks["content"][0]["content"]
    assert [item["attrs"]["checked"] for item in items] == [False, True]


@pytest.mark.parametrize(
    "content",
    [
        {},
        {"type": "unknown"},
        {"type": "doc", "content": [{"type": "unknown"}]},
        {"type": "doc", "content": [{"type": "text", "text": "Invalid at root"}]},
        {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "image"}]}]},
        [],
    ],
)
def test_invalid_editor_schema_is_rejected(content):
    with pytest.raises(ValidationError):
        content_to_markdown(content)


@pytest.mark.parametrize("markdown", [42, {}, [], True])
def test_markdown_requires_string(markdown):
    with pytest.raises(ValidationError):
        markdown_to_content(markdown)


def test_missing_ambiguous_and_oversized_payloads_are_rejected():
    with pytest.raises(ValidationError):
        normalize_content()
    with pytest.raises(ValidationError):
        normalize_content(content={}, markdown="both")
    with pytest.raises(ValidationError, match="too large"):
        markdown_to_content("x" * 1_000_001)
