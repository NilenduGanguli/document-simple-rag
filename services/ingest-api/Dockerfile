FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Install shared package
COPY shared/ /shared/
RUN pip install --no-cache-dir /shared/

# Service dependencies
COPY services/ingest-api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/ingest-api/app/ ./app/

RUN useradd -u 1001 -r appuser
USER 1001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
