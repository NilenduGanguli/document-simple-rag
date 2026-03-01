# chromadb-uvicorn

A ChromaDB server running on **uvicorn**, built on `python:3.12-slim`.
This is a custom image — it does **not** use the official ChromaDB Docker image.

## How it works

| File | Purpose |
|---|---|
| `server.py` | Creates the ChromaDB ASGI app via `chromadb.server.fastapi.FastAPI` and exposes it as `app` for uvicorn |
| `Dockerfile` | Builds the image from `python:3.12-slim`, installs dependencies, and starts uvicorn |
| `docker-compose.yml` | Convenience compose file with a named volume for persistence and a healthcheck |
| `requirements.txt` | `chromadb` + `uvicorn[standard]` |

## Quick start

### Docker Compose (recommended)

```bash
docker compose up --build
```

### Docker (plain)

```bash
# Build
docker build -t chromadb-uvicorn .

# Run with a local volume for persistence
docker run -d \
  --name chromadb \
  -p 8000:8000 \
  -v chroma_data:/chroma/chroma \
  chromadb-uvicorn
```

### Local (no Docker)

```bash
pip install -r requirements.txt
python server.py
```

## Configuration

All settings are controlled via environment variables:

| Variable | Default | Description |
|---|---|---|
| `IS_PERSISTENT` | `1` | Set to `0` to run in-memory only |
| `PERSIST_DIRECTORY` | `/chroma/chroma` | Path for persistent storage |
| `ALLOW_RESET` | `false` | Allow `DELETE /api/v1/reset` endpoint |
| `ANONYMIZED_TELEMETRY` | `false` | Disable usage telemetry |
| `CHROMA_HOST` | `0.0.0.0` | Listen address (used when running `python server.py`) |
| `CHROMA_PORT` | `8000` | Listen port (used when running `python server.py`) |
| `LOG_LEVEL` | `info` | Uvicorn log level |

## Endpoints

The server exposes the full ChromaDB HTTP API:

```
GET  /api/v1/heartbeat          # Health check
GET  /api/v1/version            # ChromaDB version
GET  /api/v1/collections        # List collections
POST /api/v1/collections        # Create collection
...
```

## Example client usage

```python
import chromadb

client = chromadb.HttpClient(host="localhost", port=8000)

# Verify connection
print(client.heartbeat())

# Create a collection and add documents
collection = client.get_or_create_collection("my_collection")
collection.add(
    documents=["Hello world", "ChromaDB on uvicorn"],
    ids=["doc1", "doc2"],
)

results = collection.query(query_texts=["hello"], n_results=1)
print(results)
```
