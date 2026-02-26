FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y gcc curl && rm -rf /var/lib/apt/lists/*

COPY shared/ /shared/
RUN pip install --no-cache-dir /shared/

COPY services/retrieval-api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/retrieval-api/app/ ./app/

RUN useradd -u 1001 -r appuser && chown -R appuser /app
USER 1001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
