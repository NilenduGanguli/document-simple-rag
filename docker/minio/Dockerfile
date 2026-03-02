FROM debian:12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# MinIO server binary
# ---------------------------------------------------------------------------
RUN curl -fsSL https://dl.min.io/server/minio/release/linux-amd64/minio \
        -o /usr/local/bin/minio \
    && chmod +x /usr/local/bin/minio

# ---------------------------------------------------------------------------
# MinIO client (mc) — for bucket initialization
# ---------------------------------------------------------------------------
RUN curl -fsSL https://dl.min.io/client/mc/release/linux-amd64/mc \
        -o /usr/local/bin/mc \
    && chmod +x /usr/local/bin/mc

RUN mkdir -p /data
VOLUME ["/data"]

COPY docker/minio/start.sh /start.sh
RUN chmod +x /start.sh

EXPOSE 9000 9001

CMD ["/start.sh"]
