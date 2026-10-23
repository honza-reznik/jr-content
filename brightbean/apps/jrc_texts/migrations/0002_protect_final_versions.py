from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("jrc_texts", "0001_initial")]

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE FUNCTION jrc_texts_protect_final() RETURNS trigger
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
                CREATE CONSTRAINT TRIGGER jrc_texts_protect_final
                AFTER UPDATE OR DELETE ON jrc_texts_textversion
                DEFERRABLE INITIALLY DEFERRED
                FOR EACH ROW EXECUTE FUNCTION jrc_texts_protect_final();
            """,
            reverse_sql="""
                DROP TRIGGER jrc_texts_protect_final ON jrc_texts_textversion;
                DROP FUNCTION jrc_texts_protect_final();
            """,
        ),
    ]
