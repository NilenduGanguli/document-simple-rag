#!/bin/bash
set -e

# Create log dir for supervisor
mkdir -p /var/log/supervisor

# Ensure data directories exist with correct ownership
mkdir -p /var/lib/redis && chown redis:redis /var/lib/redis
mkdir -p /var/lib/rabbitmq && chown rabbitmq:rabbitmq /var/lib/rabbitmq

# Enable RabbitMQ management plugin
rabbitmq-plugins enable rabbitmq_management 2>/dev/null || true

# Initialize PostgreSQL cluster on first boot
/init-postgres.sh

echo "[infra] starting supervisord..."
exec /usr/local/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
