"""Tiptap JSON ↔ Markdown through the prototype's server-side schema."""

import json
import subprocess

from django.core.exceptions import ValidationError

MAX_CONTENT_BYTES = 1_000_000


def normalize_content(*, content=None, markdown=None, plain=False):
    if (content is None) == (markdown is None):
        raise ValidationError("Provide exactly one of content and markdown.")
    payload = {"markdown": markdown} if markdown is not None else {"content": content}
    try:
        encoded = json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Content must be JSON.") from exc
    if len(encoded.encode()) > MAX_CONTENT_BYTES:
        raise ValidationError("Text is too large.")
    if plain:
        text = markdown if markdown is not None else content.get("text") if isinstance(content, dict) else None
        if not isinstance(text, str):
            raise ValidationError('Plain content must contain a string: {"text": "..."}.')
        return {"text": text}, text
    try:
        result = subprocess.run(
            ["node", "/opt/jrc-texts/bridge.mjs"],
            input=encoded,
            text=True,
            capture_output=True,
            timeout=10,
            check=True,
        )
        normalized = json.loads(result.stdout)
    except subprocess.CalledProcessError as exc:
        raise ValidationError("Content does not match the editor schema.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValidationError("Text conversion took too long.") from exc
    return normalized["content"], normalized["text"]


def markdown_to_content(text):
    return normalize_content(markdown=text)[0]


def content_to_markdown(content):
    return normalize_content(content=content)[1]


# P1-B30: for a rich document ``TextVersion.text`` is Markdown, the exchange format of the API and MCP
# (``<`` is stored as ``&lt;``, ``&`` as ``&amp;``, ``*`` as ``\*``...). It is never shown to people as
# text: a template would escape those entities a second time. Pages read the editor content instead.
_LISTS = ("bulletList", "orderedList", "taskList")


def content_to_plain_text(content):
    """Readable plain text of editor content (Tiptap JSON) for previews; the template escapes it once.

    Blocks are separated by an empty line, list items keep their bullet, number or task box (nested
    items are indented), a hard break is a new line and formatting marks are dropped. Anything that is
    not editor content gives "".
    """
    if not isinstance(content, dict) or content.get("type") != "doc":
        return ""
    return "\n\n".join(_blocks(content.get("content"))).strip("\n")


def _children(node):
    content = node.get("content")
    return [child for child in content if isinstance(child, dict)] if isinstance(content, list) else []


def _inline(node):
    parts = []
    for child in _children(node):
        if child.get("type") == "text" and isinstance(child.get("text"), str):
            parts.append(child["text"])
        elif child.get("type") == "hardBreak":
            parts.append("\n")
    return "".join(parts)


def _blocks(nodes):
    blocks = []
    for node in nodes if isinstance(nodes, list) else []:
        if not isinstance(node, dict):
            continue
        kind = node.get("type")
        if kind in ("paragraph", "heading", "codeBlock"):
            blocks.append(_inline(node))
        elif kind == "blockquote":
            inner = "\n\n".join(_blocks(_children(node)))
            blocks.append("\n".join(f"> {line}" if line else ">" for line in inner.split("\n")))
        elif kind == "horizontalRule":
            blocks.append("———")
        elif kind in _LISTS:
            blocks.append("\n".join(_list_lines(node, indent="")))
    return blocks


def _list_lines(node, indent):
    attrs = node.get("attrs") if isinstance(node.get("attrs"), dict) else {}
    number = attrs.get("start") if isinstance(attrs.get("start"), int) else 1
    lines = []
    for item in _children(node):
        item_attrs = item.get("attrs") if isinstance(item.get("attrs"), dict) else {}
        if node.get("type") == "orderedList":
            marker = f"{number}. "
            number += 1
        elif node.get("type") == "taskList":
            marker = "☑ " if item_attrs.get("checked") else "☐ "
        else:
            marker = "• "
        inner = indent + " " * len(marker)
        own = [child for child in _children(item) if child.get("type") not in _LISTS]
        text = "\n".join(_blocks(own)).replace("\n", "\n" + inner)
        lines.append(indent + marker + text)
        for child in _children(item):
            if child.get("type") in _LISTS:
                lines.extend(_list_lines(child, inner))
    return lines
