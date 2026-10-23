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
