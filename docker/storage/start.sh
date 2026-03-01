#!/bin/bash
set -e

mkdir -p /var/log/supervisor /data

echo "[storage] starting supervisord..."
exec /usr/local/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
