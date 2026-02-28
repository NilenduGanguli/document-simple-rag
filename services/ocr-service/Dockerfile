FROM python:3.12-slim

WORKDIR /app

COPY shared/ /shared/
RUN pip install --no-cache-dir /shared/

COPY services/ocr-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/ocr-service/app/ ./app/

# Load environment defaults at container startup
COPY services/ocr-service/set-env.sh .
RUN chmod +x set-env.sh

RUN useradd -u 1001 -r appuser
USER 1001

CMD ["/bin/sh", "-c", ". /app/set-env.sh && exec python -m app.main"]
