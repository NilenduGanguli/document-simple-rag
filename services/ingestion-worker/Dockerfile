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

RUN useradd -u 1001 -r appuser && chown -R appuser /app
USER 1001

CMD ["python", "-m", "app.main"]
