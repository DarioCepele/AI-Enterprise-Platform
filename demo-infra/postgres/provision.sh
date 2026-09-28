#!/bin/sh
# One role and one database per service, created if missing, on every `up`.
#
# Least privilege: each service connects as its own role, owns its own
# database, and cannot open anyone else's. The superuser in .env is used here
# and nowhere else.
#
# Idempotent, and run by the `postgres-init` job on every `docker compose up`,
# not only on the first initialisation of the volume: an existing volume gets
# what is missing, and a rotated password is applied by editing .env and
# running `up` again. Values reach SQL as psql variables (`:'name'`), which
# psql quotes itself: a password with a quote in it is a password, not SQL.
set -eu

export PGPASSWORD="$POSTGRES_PASSWORD"

admin() {
    database=$1
    shift
    psql -v ON_ERROR_STOP=1 -q -h "${POSTGRES_HOST:-postgres}" \
        -p "${POSTGRES_PORT:-5432}" -U "$POSTGRES_USER" -d "$database" "$@"
}

provision() {
    admin postgres -v role="$1" -v password="$2" -v database="$3" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN', :'role')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'role')\gexec
SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L', :'role', :'password')\gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'database', :'role')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'database')\gexec
SELECT format('ALTER DATABASE %I OWNER TO %I', :'database', :'role')\gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'database')\gexec
SQL
    echo "provisioned: database $3, role $1"
}

provision master_svc "$MASTER_DB_PASSWORD" master
provision memory_svc "$MEMORY_DB_PASSWORD" memory
provision process_svc "$PROCESS_DB_PASSWORD" processes
provision agents_svc "$AGENTS_DB_PASSWORD" agents

# pgvector is created by the superuser: the memory service only uses it.
admin memory -c "CREATE EXTENSION IF NOT EXISTS vector"
echo "provisioned: extension vector in memory"
