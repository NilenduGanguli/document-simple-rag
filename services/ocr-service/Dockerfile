FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-fra \
    tesseract-ocr-deu \
    tesseract-ocr-spa \
    tesseract-ocr-chi-sim \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Hard image-level defaults for Tesseract (overridable via set-env.sh or docker-compose)
ENV TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata
ENV OMP_THREAD_LIMIT=2

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
