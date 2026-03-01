import os

import uvicorn

# chromadb.app builds the ASGI app using chromadb.config.Settings(), which is a
# pydantic BaseSettings class that reads all fields from environment variables
# (IS_PERSISTENT, PERSIST_DIRECTORY, ALLOW_RESET, etc.) automatically.
from chromadb.app import app  # noqa: F401 – re-exported for uvicorn

if __name__ == "__main__":
    host = os.getenv("CHROMA_HOST", "0.0.0.0")
    port = int(os.getenv("CHROMA_PORT", "8000"))
    log_level = os.getenv("LOG_LEVEL", "info")

    uvicorn.run(
        "server:app",
        host=host,
        port=port,
        log_level=log_level,
    )
