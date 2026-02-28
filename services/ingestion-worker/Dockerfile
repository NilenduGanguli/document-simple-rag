FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    libmupdf-dev \
    && rm -rf /var/lib/apt/lists/*

COPY shared/ /shared/
RUN pip install --no-cache-dir /shared/

COPY services/ingestion-worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/ingestion-worker/app/ ./app/

# Load environment defaults at container startup
COPY services/ingestion-worker/set-env.sh .
RUN chmod +x set-env.sh

RUN useradd -u 1001 -r appuser && chown -R appuser /app
USER 1001

CMD ["/bin/sh", "-c", ". /app/set-env.sh && exec python -m app.main"]
