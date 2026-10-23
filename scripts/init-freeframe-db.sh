#!/bin/sh
# Běží v PostgreSQL, také nad existujícím datovým svazkem.
set -eu
psql -X --set ON_ERROR_STOP=1 --dbname "$PGDATABASE" <<'SQL'
SELECT format('CREATE DATABASE %I', 'freeframe')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'freeframe')
\gexec
SQL
