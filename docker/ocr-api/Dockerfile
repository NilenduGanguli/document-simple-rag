FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY docker/ocr-api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY services/ocr-api/app/ /app/app/

RUN useradd -u 1001 -r appuser \
    && chown -R appuser:appuser /app

EXPOSE 8002

USER 1001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002", "--workers", "1"]
