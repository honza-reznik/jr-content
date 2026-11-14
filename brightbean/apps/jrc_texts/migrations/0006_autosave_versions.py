# JR Content: přidáno 2026-11-14 (blok P2-B08), AGPL-3.0.
"""Automatic saving of texts (P2-B08).

A saved version stays open for its author on the same device for 15 minutes after the last edit:
the database rule that protects saved versions allows exactly that change (text, revision and
times; nothing else, and only within the window). Drafts of the old Edit/Save editor become saved
versions (nothing typed is lost); drafts that differ in nothing from their base are removed.
"""

from django.db import migrations, models
from django.utils import timezone

_PROTECT = """
CREATE OR REPLACE FUNCTION jrc_texts_protect_final() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.state = 'final' THEN
        -- Django deletes children before the parent document.
        IF TG_OP = 'DELETE' THEN
            IF NOT EXISTS (SELECT 1 FROM jrc_texts_textdocument WHERE id = OLD.document_id)
               AND NOT EXISTS (SELECT 1 FROM jrc_texts_textversion WHERE id = OLD.id)
            THEN
                RETURN NULL;
            END IF;
        END IF;
        -- Only a genuinely deleted author may be anonymized;
        -- the version's remaining fields must stay unchanged.
        IF TG_OP = 'UPDATE' THEN
            -- The collector may clear self-references just before
            -- removing the entire document. Never allow relocation.
            IF NEW.based_on_id IS NULL
               AND to_jsonb(OLD) - 'based_on_id' = to_jsonb(NEW) - 'based_on_id'
               AND NOT EXISTS (SELECT 1 FROM jrc_texts_textdocument WHERE id = OLD.document_id)
               AND NOT EXISTS (SELECT 1 FROM jrc_texts_textversion WHERE id = OLD.id)
            THEN
                RETURN NULL;
            END IF;
            IF OLD.created_by_id IS NOT NULL AND NEW.created_by_id IS NULL
               AND to_jsonb(OLD) - 'created_by_id' = to_jsonb(NEW) - 'created_by_id'
               AND NOT EXISTS (SELECT 1 FROM accounts_user WHERE id = OLD.created_by_id)
            THEN
                RETURN NULL;
            END IF;
            -- P2-B08: the author keeps writing into an open version from the same device. Only the
            -- text, its revision and the times change, and only within the window after the last edit
            -- (15 minutes in the application, one minute of tolerance here).
            IF NEW.state = 'final' AND OLD.device <> '' AND OLD.created_by_id IS NOT NULL
               AND OLD.edited_at IS NOT NULL AND OLD.edited_at >= now() - interval '16 minutes'
               AND NEW.edited_at IS NOT NULL AND NEW.edited_at >= OLD.edited_at
               AND NEW.revision > OLD.revision
               AND to_jsonb(OLD) - ARRAY['content', 'text', 'char_count', 'revision', 'edited_at', 'finalized_at']
                   = to_jsonb(NEW) - ARRAY['content', 'text', 'char_count', 'revision', 'edited_at', 'finalized_at']
            THEN
                RETURN NULL;
            END IF;
        END IF;
        RAISE EXCEPTION 'Saved text versions cannot be changed or deleted'
            USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;
"""

_PROTECT_BEFORE = """
CREATE OR REPLACE FUNCTION jrc_texts_protect_final() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.state = 'final' THEN
        IF TG_OP = 'DELETE' THEN
            IF NOT EXISTS (SELECT 1 FROM jrc_texts_textdocument WHERE id = OLD.document_id)
               AND NOT EXISTS (SELECT 1 FROM jrc_texts_textversion WHERE id = OLD.id)
            THEN
                RETURN NULL;
            END IF;
        END IF;
        IF TG_OP = 'UPDATE' THEN
            IF NEW.based_on_id IS NULL
               AND to_jsonb(OLD) - 'based_on_id' = to_jsonb(NEW) - 'based_on_id'
               AND NOT EXISTS (SELECT 1 FROM jrc_texts_textdocument WHERE id = OLD.document_id)
               AND NOT EXISTS (SELECT 1 FROM jrc_texts_textversion WHERE id = OLD.id)
            THEN
                RETURN NULL;
            END IF;
            IF OLD.created_by_id IS NOT NULL AND NEW.created_by_id IS NULL
               AND to_jsonb(OLD) - 'created_by_id' = to_jsonb(NEW) - 'created_by_id'
               AND NOT EXISTS (SELECT 1 FROM accounts_user WHERE id = OLD.created_by_id)
            THEN
                RETURN NULL;
            END IF;
        END IF;
        RAISE EXCEPTION 'Saved text versions cannot be changed or deleted'
            USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;
"""


def _is_empty_document(content):
    """An empty document holds only empty paragraphs; an image or a divider has no text but is content."""
    if not content:
        return True
    nodes = content.get("content") if isinstance(content, dict) else None
    return all(
        isinstance(node, dict) and node.get("type") == "paragraph" and not node.get("content")
        for node in nodes or []
    ) and set(content) <= {"type", "content"}


def drafts_to_versions(apps, schema_editor):
    """Keep what was typed in the old editor's drafts as saved versions."""
    TextVersion = apps.get_model("jrc_texts", "TextVersion")
    for draft in TextVersion.objects.filter(state="draft").select_related("based_on").order_by("created_at"):
        base = draft.based_on
        unchanged = draft.content == base.content if base is not None else _is_empty_document(draft.content)
        if unchanged:
            draft.delete()
            continue
        draft.state = "final"
        draft.finalized_at = draft.draft_heartbeat_at or draft.created_at or timezone.now()
        draft.created_by_id = draft.created_by_id or draft.draft_owner_id
        draft.draft_owner = None
        draft.draft_heartbeat_at = None
        draft.save(update_fields=["state", "finalized_at", "created_by", "draft_owner", "draft_heartbeat_at"])


class Migration(migrations.Migration):
    dependencies = [("jrc_texts", "0005_textversionlabel_textversionnamechange")]

    operations = [
        migrations.AddField(
            model_name="textversion",
            name="device",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="textversion",
            name="edited_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunSQL(sql=_PROTECT, reverse_sql=_PROTECT_BEFORE),
        migrations.RunPython(drafts_to_versions, migrations.RunPython.noop),
    ]
