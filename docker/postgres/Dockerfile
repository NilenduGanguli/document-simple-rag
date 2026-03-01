FROM postgres:16-bookworm

# The official postgres image automatically runs all *.sql and *.sh files placed in
# /docker-entrypoint-initdb.d/ on first startup (when the data directory is empty).
# User, password, and database are created via POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB.

COPY infra/postgres/01_init.sql /docker-entrypoint-initdb.d/01_init.sql

EXPOSE 5432
