FROM python:3.12-slim

# ---------------------------------------------------------------------------
# System packages: curl for downloading MinIO binaries
# supervisor is installed via pip after chromadb deps
# ---------------------------------------------------------------------------
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
# MinIO client binary (mc) — used for bucket initialization
# ---------------------------------------------------------------------------
RUN curl -fsSL https://dl.min.io/client/mc/release/linux-amd64/mc \
        -o /usr/local/bin/mc \
    && chmod +x /usr/local/bin/mc

# ---------------------------------------------------------------------------
# ChromaDB — Python ASGI server (same as chromadb-uvicorn/)
# ---------------------------------------------------------------------------
WORKDIR /chroma

COPY chromadb-uvicorn/requirements.txt /chroma/requirements.txt
RUN pip install --no-cache-dir -r /chroma/requirements.txt \
    && pip install --no-cache-dir supervisor

COPY chromadb-uvicorn/server.py /chroma/server.py

RUN mkdir -p /chroma/chroma

# ---------------------------------------------------------------------------
# MinIO bucket init script (inline — waits until MinIO is ready)
# ---------------------------------------------------------------------------
COPY docker/storage/minio_init.sh /minio_init.sh
RUN chmod +x /minio_init.sh

# ---------------------------------------------------------------------------
# Supervisord configuration
# ---------------------------------------------------------------------------
RUN mkdir -p /etc/supervisor/conf.d /var/log/supervisor
COPY docker/storage/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
COPY docker/storage/start.sh /start.sh
RUN chmod +x /start.sh

EXPOSE 8000 9000 9001

VOLUME ["/chroma/chroma", "/data"]

CMD ["/start.sh"]
