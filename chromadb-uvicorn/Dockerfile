FROM python:3.12-slim

WORKDIR /chroma

# Install build tools required by some chromadb transitive dependencies
# RUN apt-get update \
#     && apt-get install -y --no-install-recommends \
#         build-essential \
#     && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the server entrypoint
COPY server.py .

# Create the persistent-storage directory and declare it as a volume
RUN mkdir -p /chroma/chroma
VOLUME ["/chroma/chroma"]

# --- ChromaDB runtime configuration ---
# Persist data between container restarts
ENV IS_PERSISTENT=1
# Directory where ChromaDB stores its data
ENV PERSIST_DIRECTORY=/chroma/chroma
# Do not allow clients to call the reset endpoint by default
ENV ALLOW_RESET=false
# Disable usage telemetry
ENV ANONYMIZED_TELEMETRY=false

# Uvicorn listen address and port
ENV CHROMA_HOST=0.0.0.0
ENV CHROMA_PORT=8000

EXPOSE 8000

# Run uvicorn serving the ChromaDB ASGI app defined in server.py
CMD ["uvicorn", "server:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--log-level", "info"]
