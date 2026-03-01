FROM debian:bookworm-slim

# ---------------------------------------------------------------------------
# Install PostgreSQL 16, Redis; supervisor is installed via pip below
# ---------------------------------------------------------------------------
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gnupg curl ca-certificates lsb-release gosu python3 python3-pip \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        | gpg --dearmor -o /etc/apt/trusted.gpg.d/postgresql.gpg \
    && echo "deb https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16 redis-server \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Install RabbitMQ 4.x + compatible Erlang from CloudSmith
# ---------------------------------------------------------------------------
RUN curl -1sLf "https://keys.openpgp.org/vks/v1/by-fingerprint/0A9AF2115F4687BD29803A206B73A36E6026DFCA" \
        | gpg --dearmor | tee /usr/share/keyrings/com.rabbitmq.team.gpg > /dev/null \
    && curl -1sLf "https://github.com/rabbitmq/signing-keys/releases/download/3.0/cloudsmith.rabbitmq-erlang.E495BB49CC4BBE5B.key" \
        | gpg --dearmor | tee /usr/share/keyrings/rabbitmq.E495BB49CC4BBE5B.gpg > /dev/null \
    && curl -1sLf "https://github.com/rabbitmq/signing-keys/releases/download/3.0/cloudsmith.rabbitmq-server.9F4587F226208342.key" \
        | gpg --dearmor | tee /usr/share/keyrings/rabbitmq.9F4587F226208342.gpg > /dev/null \
    && printf '%s\n' \
        "deb [arch=amd64 signed-by=/usr/share/keyrings/rabbitmq.E495BB49CC4BBE5B.gpg] https://ppa1.novemberain.com/rabbitmq/rabbitmq-erlang/deb/debian bookworm main" \
        "deb [arch=amd64 signed-by=/usr/share/keyrings/rabbitmq.9F4587F226208342.gpg] https://ppa1.novemberain.com/rabbitmq/rabbitmq-server/deb/debian bookworm main" \
        > /etc/apt/sources.list.d/rabbitmq.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        erlang-base erlang-asn1 erlang-crypto erlang-eldap erlang-ftp \
        erlang-inets erlang-mnesia erlang-os-mon erlang-parsetools \
        erlang-public-key erlang-runtime-tools erlang-snmp erlang-ssl \
        erlang-syntax-tools erlang-tftp erlang-tools erlang-xmerl \
        rabbitmq-server \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Install supervisor via pip (available globally as 'supervisord')
# ---------------------------------------------------------------------------
RUN pip3 install --no-cache-dir --break-system-packages supervisor

# ---------------------------------------------------------------------------
# PostgreSQL setup
# ---------------------------------------------------------------------------
ENV PGDATA=/var/lib/postgresql/data
ENV POSTGRES_USER=raguser
ENV POSTGRES_PASSWORD=ragpassword123
ENV POSTGRES_DB=ragdb

COPY infra/postgres/01_init.sql /docker-init-sql/01_init.sql
COPY docker/infra/init-postgres.sh /init-postgres.sh
RUN chmod +x /init-postgres.sh

# ---------------------------------------------------------------------------
# Redis setup
# ---------------------------------------------------------------------------
COPY infra/redis/redis.conf /etc/redis/redis.conf
RUN mkdir -p /var/lib/redis && chown redis:redis /var/lib/redis

# ---------------------------------------------------------------------------
# RabbitMQ setup
# ---------------------------------------------------------------------------
COPY infra/rabbitmq/rabbitmq.conf /etc/rabbitmq/rabbitmq.conf
COPY infra/rabbitmq/definitions.json /etc/rabbitmq/definitions.json
RUN chown -R rabbitmq:rabbitmq /etc/rabbitmq

# ---------------------------------------------------------------------------
# Supervisord configuration
# ---------------------------------------------------------------------------
RUN mkdir -p /etc/supervisor/conf.d /var/log/supervisor
COPY docker/infra/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
COPY docker/infra/start.sh /start.sh
RUN chmod +x /start.sh

EXPOSE 5432 6379 5672 15672

CMD ["/start.sh"]
