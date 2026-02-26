FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y gcc && rm -rf /var/lib/apt/lists/*

COPY shared/ /shared/
RUN pip install --no-cache-dir /shared/

COPY services/embedding-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/embedding-service/app/ ./app/

RUN useradd -u 1001 -r appuser && chown -R appuser /app
USER 1001

CMD ["python", "-m", "app.main"]
