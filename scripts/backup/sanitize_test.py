"""Anonymizace testovací kopie. Jen v Dockeru, bez výpisu adres a změn UUID/hesel."""
import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
from pathlib import Path

from jrc_backup import load_env_file


def anonymous_email(value):
    if not value or value.rsplit("@", 1)[-1].lower().endswith(".invalid"):
        return value
    return hashlib.sha256(value.strip().lower().encode()).hexdigest() + "@example.invalid"


def require_test_project(project):
    if not re.fullmatch(r"jrc-[a-z0-9-]+", project or ""):
        raise ValueError("Anonymizace vyžaduje projekt jrc-<název>; jrc je zakázán.")


def query(database, sql):
    result = subprocess.run(
        ["psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "--dbname", database],
        input=sql, text=True, capture_output=True, timeout=120,
    )
    if result.returncode:
        # Constraint errors may contain the original address. Never forward stderr.
        raise RuntimeError("Anonymizace databáze selhala; aplikace musí zůstat zastavené.")
    return result.stdout.strip()


def sanitize_database(database):
    # Also covers optional billing, OAuth, guests and share-session mailboxes in
    # older backups. Never rewrite IDs: managed accounts derive their UUID from
    # the Brightbean user UUID, not from their address.
    columns = json.loads(query(database, r"""
        SELECT coalesce(json_agg(json_build_array(table_name, column_name)), '[]')
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND data_type IN ('character varying', 'text', 'character')
          AND (column_name = 'email' OR column_name LIKE '%\_email' ESCAPE '\'
               OR (table_name = 'common_email_suppression' AND column_name = 'address'));
    """))
    required = {"brightbean": "accounts_user", "freeframe": "users"}[database]
    if [required, "email"] not in columns:
        raise RuntimeError("Chybí tabulka uživatelů; anonymizace se neprovede.")
    tables = set(query(database, "SELECT tablename FROM pg_tables WHERE schemaname='public';").splitlines())
    statements = ["BEGIN; SET LOCAL lock_timeout = '10s';"]
    # Drop saved one-time links and envelopes, including those in old backups.
    if "common_outgoing_email" in tables:
        statements.append("WITH changed AS (DELETE FROM common_outgoing_email RETURNING 1) "
                          "SELECT 'outgoing_email=' || count(*) FROM changed;")
    if "notifications_delivery" in tables:
        statements.append("WITH changed AS (DELETE FROM notifications_delivery "
                          "WHERE channel='email' AND status IN ('pending', 'failed') RETURNING 1) "
                          "SELECT 'notification_delivery=' || count(*) FROM changed;")
    for table, column in columns:
        if table == "common_outgoing_email":
            continue
        table_sql = '"' + table.replace('"', '""') + '"'
        column_sql = '"' + column.replace('"', '""') + '"'
        # Table/column names are catalog values. Keep output labels constant so
        # even a surprising table name cannot introduce an address into logs.
        label = len(statements)
        statements.append(f"""
            WITH changed AS (
                UPDATE {table_sql} SET {column_sql} =
                    encode(sha256(convert_to(lower(btrim({column_sql})), 'UTF8')), 'hex') || '@example.invalid'
                WHERE {column_sql} <> '' AND lower(split_part({column_sql}, '@', -1)) NOT LIKE '%.invalid'
                RETURNING 1
            ) SELECT 'email_field_{label}=' || count(*) FROM changed;
        """)
    statements.append("COMMIT;")
    return query(database, "\n".join(statements))


def sanitize(project, workspace):
    require_test_project(project)
    env_path = Path(workspace) / ".env"
    if env_path.is_symlink() or not env_path.is_file():
        raise ValueError("Chybí běžný soubor .env.")
    env = load_env_file(env_path)
    # BOTH applications use this mailbox to log in to the same service account.
    configured_sync = os.environ.get("JRC_FF_SYNC_EMAIL") or env.get("JRC_FF_SYNC_EMAIL") or "jrc-sync@example.com"
    if configured_sync[:1] in ("'", '"'):
        configured_sync = (shlex.split(configured_sync, comments=True) or [""])[0] or "jrc-sync@example.com"
    sync = anonymous_email(configured_sync)
    for database in ("brightbean", "freeframe"):
        for line in sanitize_database(database).splitlines():
            print(f"{database}.{line}")
    original = env_path.read_text()
    line = f"JRC_FF_SYNC_EMAIL={sync}"
    pattern = r"^[ \t]*(?:export[ \t]+)?JRC_FF_SYNC_EMAIL[ \t]*=.*$"
    updated = re.sub(pattern, line, original, flags=re.M)
    if not re.search(pattern, original, flags=re.M):
        updated = original.rstrip("\n") + "\n" + line + "\n"
    if updated != original:
        temporary = env_path.with_name(".jrc-env.sanitize.env.local")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(updated)
            temporary.replace(env_path)
        finally:
            temporary.unlink(missing_ok=True)
    print(f"configuration_changed={int(updated != original)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--workspace", default="/workspace")
    args = parser.parse_args()
    try:
        sanitize(args.project, args.workspace)
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired):
        raise SystemExit("Anonymizace selhala; aplikace ponechte zastavené. Žádné adresy se nevypisují.") from None
