FROM python:3.12-slim

# ---------------------------------------------------------------------------
# System packages
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ curl ca-certificates \
        libmupdf-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ---------------------------------------------------------------------------
# Shared rag-shared package (installed first for layer caching)
# ---------------------------------------------------------------------------
COPY shared/ /shared/
RUN pip install --no-cache-dir /shared/

# ---------------------------------------------------------------------------
# Consolidated requirements
# ---------------------------------------------------------------------------
COPY docker/backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# ---------------------------------------------------------------------------
# Unified backend application
# ---------------------------------------------------------------------------
COPY services/unified-backend/app/ /app/app/

# ---------------------------------------------------------------------------
# model-init
# ---------------------------------------------------------------------------
COPY services/model-init/model_init.py  /app/model_init/model_init.py
COPY services/model-init/set-env.sh     /app/model_init/set-env.sh
RUN chmod +x /app/model_init/set-env.sh

# ---------------------------------------------------------------------------
# Start script + DB/S3 bootstrap
# ---------------------------------------------------------------------------
COPY docker/backend/start.sh     /app/start.sh
COPY docker/backend/db_ensure.py /app/db_ensure.py
COPY docker/backend/s3_ensure.py /app/s3_ensure.py
RUN chmod +x /app/start.sh

# ---------------------------------------------------------------------------
# Non-root user
# ---------------------------------------------------------------------------
RUN useradd -u 1001 -r appuser \
    && chown -R appuser:appuser /app

EXPOSE 8000

CMD ["/app/start.sh"]
