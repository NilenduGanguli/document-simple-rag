#!/bin/bash
# One-time PostgreSQL cluster initialization.
# Runs at container startup only when PGDATA is empty.
set -e

if [ -d "$PGDATA/base" ]; then
    echo "[init-postgres] cluster already exists, skipping init"
    exit 0
fi

echo "[init-postgres] initializing PostgreSQL cluster..."
gosu postgres initdb \
    --encoding=UTF8 \
    --locale=en_US.UTF-8 \
    -D "$PGDATA"

# Allow password authentication from any container on the rag-network
cat >> "$PGDATA/pg_hba.conf" <<'EOF'
host    all             all             0.0.0.0/0               md5
EOF

# Listen on all interfaces
echo "listen_addresses = '*'" >> "$PGDATA/postgresql.conf"

# Start temporarily to create role, database, and schema
gosu postgres pg_ctl -D "$PGDATA" -o "-c listen_addresses=''" \
    -w start

gosu postgres psql -v ON_ERROR_STOP=1 --username postgres <<-EOSQL
    CREATE USER ${POSTGRES_USER} WITH PASSWORD '${POSTGRES_PASSWORD}';
    CREATE DATABASE ${POSTGRES_DB} OWNER ${POSTGRES_USER};
    GRANT ALL PRIVILEGES ON DATABASE ${POSTGRES_DB} TO ${POSTGRES_USER};
EOSQL

gosu postgres psql -v ON_ERROR_STOP=1 \
    --username postgres \
    --dbname "${POSTGRES_DB}" \
    -f /docker-init-sql/01_init.sql

# Grant full access on all objects to raguser
gosu postgres psql -v ON_ERROR_STOP=1 --username postgres --dbname "${POSTGRES_DB}" <<-EOSQL
    GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO ${POSTGRES_USER};
    GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO ${POSTGRES_USER};
    GRANT ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public TO ${POSTGRES_USER};
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO ${POSTGRES_USER};
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO ${POSTGRES_USER};
EOSQL

gosu postgres pg_ctl -D "$PGDATA" -m fast -w stop

echo "[init-postgres] initialization complete"
