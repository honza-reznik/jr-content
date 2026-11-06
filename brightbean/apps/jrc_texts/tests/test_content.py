import pytest
from django.core.exceptions import ValidationError

from apps.jrc_texts.content import content_to_markdown, content_to_plain_text, markdown_to_content, normalize_content


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


# ---- P1-B30: plain text for previews comes from the editor content, never from the Markdown ----------------


@pytest.mark.parametrize(
    "markdown, expected",
    [
        ("<script>alert(1)</script> & \"q\" 's'", "<script>alert(1)</script> & \"q\" 's'"),
        ("a < b > c &amp; d", "a < b > c & d"),
        ("Doslovné \\*hvězdy\\* a \\\\ lomítko", "Doslovné *hvězdy* a \\ lomítko"),
        ("# Nadpis\n\nOdstavec s **tučným** a *kurzívou*.", "Nadpis\n\nOdstavec s tučným a kurzívou."),
        ("- První\n  - Vnořená\n- Druhá", "• První\n  • Vnořená\n• Druhá"),
        ("5. Pátý\n6. Šestý", "5. Pátý\n6. Šestý"),
        ("- [ ] Natočit\n- [x] Hotovo", "☐ Natočit\n☑ Hotovo"),
        ("> Citace\n\n```\nkód <b>\n```", "> Citace\n\nkód <b>"),
        ("Řádek  \ndalší", "Řádek\ndalší"),
        ("", ""),
    ],
)
def test_plain_text_is_what_people_wrote(markdown, expected):
    content = markdown_to_content(markdown)
    assert content_to_plain_text(content) == expected
    # The stored Markdown differs for exactly the characters that broke the Approval preview.
    if "<" in expected or "&" in expected:
        assert content_to_markdown(content) != expected


def test_plain_text_keeps_paragraph_breaks_and_hard_breaks():
    content = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "One"}, {"type": "hardBreak"}, {"type": "text", "text": "two"}],
            },
            {"type": "horizontalRule"},
            {"type": "paragraph"},
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "Three &amp; <b>", "marks": [{"type": "bold"}]}],
            },
        ],
    }
    assert content_to_plain_text(content) == "One\ntwo\n\n———\n\n\n\nThree &amp; <b>"


@pytest.mark.parametrize("content", [None, "", "text", [], {}, {"type": "paragraph"}, {"type": "doc", "content": "x"}])
def test_plain_text_of_something_else_is_empty(content):
    assert content_to_plain_text(content) == ""
