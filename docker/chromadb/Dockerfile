FROM python:3.12-slim

WORKDIR /chroma

# Install ChromaDB and Uvicorn
COPY chromadb-uvicorn/requirements.txt /chroma/requirements.txt
RUN pip install --no-cache-dir -r /chroma/requirements.txt

# ChromaDB ASGI server entrypoint
COPY chromadb-uvicorn/server.py /chroma/server.py

# Persistent data directory
RUN mkdir -p /chroma/chroma
VOLUME ["/chroma/chroma"]

ENV IS_PERSISTENT=1
ENV PERSIST_DIRECTORY=/chroma/chroma
ENV ALLOW_RESET=false
ENV ANONYMIZED_TELEMETRY=false
ENV CHROMA_HOST=0.0.0.0
ENV CHROMA_PORT=8000

EXPOSE 8000

CMD ["uvicorn", "server:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--log-level", "info"]
