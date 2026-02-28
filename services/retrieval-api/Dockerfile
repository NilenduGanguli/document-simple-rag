FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y gcc curl && rm -rf /var/lib/apt/lists/*

COPY shared/ /shared/
RUN pip install --no-cache-dir /shared/

COPY services/retrieval-api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/retrieval-api/app/ ./app/

# Load environment defaults at container startup
COPY services/retrieval-api/set-env.sh .
RUN chmod +x set-env.sh

RUN useradd -u 1001 -r appuser && chown -R appuser /app
USER 1001

CMD ["/bin/sh", "-c", ". /app/set-env.sh && exec uvicorn app.main:app --host 0.0.0.0 --port 8001"]
