"""Unit tests; optional real PostgreSQL tests run only inside our Docker project."""
import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sanitize_test as st


class Sanitization(unittest.TestCase):
    def test_stable_hash_and_reserved_domains(self):
        email = st.anonymous_email("Person@example.org")
        self.assertEqual(email, st.anonymous_email(" person@EXAMPLE.ORG "))
        self.assertRegex(email, r"^[a-f0-9]{64}@example\.invalid$")
        for value in ("owner@example.invalid", "A@SUB.INVALID", ""):
            self.assertEqual(st.anonymous_email(value), value)

    def test_main_and_invalid_names_refused_before_any_access(self):
        for project in ("jrc", "jrc-", "jrc-x;bad", "other", None):
            with self.assertRaises(ValueError), patch.object(st, "query") as query:
                st.sanitize(project, "/does-not-exist")
            query.assert_not_called()

    def test_shell_command_refuses_main_before_docker(self):
        script = Path(st.__file__).with_name("sanitize-test.sh")
        result = subprocess.run(["sh", str(script)], env={**os.environ, "JRC_PROJECT": "jrc"},
                                text=True, capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("zakázaná", result.stderr)

    def test_idempotent_service_config_and_counts_only(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text("JRC_FF_SYNC_EMAIL='service@example.org'\nSECRET=unchanged\n")
            with patch.object(st, "sanitize_database", return_value="email_field_1=2"), contextlib.redirect_stdout(io.StringIO()) as output:
                st.sanitize("jrc-p2b01", folder)
                first = path.read_bytes()
                st.sanitize("jrc-p2b01", folder)
            self.assertEqual(first, path.read_bytes())
            self.assertNotIn("@", output.getvalue())
            self.assertIn("SECRET=unchanged", path.read_text())
            self.assertIn(st.anonymous_email("service@example.org"), path.read_text())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_database_failure_never_logs_postgres_address(self):
        with patch.object(st.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "Key: private@example.org")):
            with self.assertRaises(RuntimeError) as caught:
                st.query("brightbean", "SELECT 1")
        self.assertNotIn("@", str(caught.exception))


@unittest.skipUnless(os.environ.get("JRC_SANITIZE_DB_TEST") == "jrc-p2b01", "requires isolated Docker PostgreSQL")
class PostgreSQLSanitization(unittest.TestCase):
    def test_real_databases_identity_queues_and_idempotency(self):
        real_query = st.query
        dbs = {"brightbean": "p2b01_sanitize_bb", "freeframe": "p2b01_sanitize_ff"}
        for name in dbs.values():
            real_query("postgres", f'CREATE DATABASE "{name}"')
            self.addCleanup(real_query, "postgres", f'DROP DATABASE "{name}" WITH (FORCE)')
        real_query(dbs["brightbean"], """
            CREATE TABLE accounts_user (id text PRIMARY KEY, email text UNIQUE, password text);
            INSERT INTO accounts_user VALUES ('bb1','Person@example.org','same-password-hash'), ('bb2','owner@example.invalid','test-hash');
            CREATE TABLE account_emailaddress (id int, email text, user_id text);
            INSERT INTO account_emailaddress VALUES (1,'Person@example.org','bb1'),(2,'extra@example.org','bb1');
            CREATE TABLE members_invitation (email text);
            INSERT INTO members_invitation VALUES ('invite@example.org');
            CREATE TABLE common_outgoing_email (body_text text);
            INSERT INTO common_outgoing_email VALUES ('private@example.org');
            CREATE TABLE notifications_delivery (channel text, status text);
            INSERT INTO notifications_delivery VALUES ('email','pending'),('email','failed'),('email','delivered'),('in_app','pending');
        """)
        real_query(dbs["freeframe"], """
            CREATE TABLE users (id text PRIMARY KEY, email text UNIQUE, jrc_managed bool);
            INSERT INTO users VALUES ('uuid-v5','person@example.org',true),('service','jrc-sync@example.com',false);
        """)
        with patch.object(st, "query", side_effect=lambda database, sql: real_query(dbs[database], sql)):
            for database in dbs:
                st.sanitize_database(database)
                second = st.sanitize_database(database)
                self.assertTrue(all(line.endswith("=0") for line in second.splitlines()), second)
        self.assertEqual(real_query(dbs["brightbean"], "SELECT count(*) FROM accounts_user WHERE email NOT LIKE '%.invalid'"), "0")
        self.assertEqual(real_query(dbs["brightbean"], "SELECT password FROM accounts_user WHERE id='bb1'"), "same-password-hash")
        self.assertEqual(real_query(dbs["brightbean"], "SELECT email FROM accounts_user WHERE id='bb2'"), "owner@example.invalid")
        hashed = real_query(dbs["brightbean"], "SELECT email FROM accounts_user WHERE id='bb1'")
        self.assertEqual(real_query(dbs["freeframe"], "SELECT email FROM users WHERE id='uuid-v5' AND jrc_managed"), hashed)
        self.assertEqual(real_query(dbs["brightbean"], "SELECT count(*) FROM common_outgoing_email"), "0")
        self.assertEqual(real_query(dbs["brightbean"], "SELECT count(*) FROM notifications_delivery"), "2")
        for table in ("account_emailaddress", "members_invitation"):
            self.assertEqual(real_query(dbs["brightbean"], f"SELECT count(*) FROM {table} WHERE email NOT LIKE '%.invalid'"), "0")


if __name__ == "__main__":
    unittest.main()
