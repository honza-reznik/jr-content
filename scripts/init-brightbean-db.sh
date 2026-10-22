#!/bin/sh
# Běží jen v kontejneru PostgreSQL; funguje i nad již existujícím svazkem F0.
set -eu
psql -X --set ON_ERROR_STOP=1 --dbname "$PGDATABASE" <<'SQL'
SELECT format('CREATE DATABASE %I', 'brightbean')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'brightbean')
\gexec
SQL
