from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional


class Settings(BaseSettings):
    # Database
    database_url: str = Field(default="postgresql://raguser:ragpassword123@localhost:5432/ragdb")

    # Redis
    redis_url: str = Field(default="redis://localhost:6379/0")

    # RabbitMQ
    rabbitmq_url: str = Field(default="amqp://raguser:ragpassword123@localhost:5672/")

    # S3/MinIO
    s3_endpoint_url: Optional[str] = Field(default=None)
    s3_access_key: str = Field(default="minioadmin")
    s3_secret_key: str = Field(default="minioadmin123")
    s3_bucket: str = Field(default="rag-documents")
    s3_region: str = Field(default="us-east-1")
    s3_external_url: Optional[str] = Field(default=None)

    # API Keys (comma-separated)
    api_keys: str = Field(default="dev-api-key-1")

    # Model paths
    model_dest: str = Field(default="/models")
    model_version: str = Field(default="local-docker-compose")

    # ONNX settings
    onnx_pool_size: int = Field(default=2)
    onnx_threads_per_session: int = Field(default=2)
    embedding_batch_size: int = Field(default=16)
    prefetch_queue_size: int = Field(default=4)

    # Ingestion
    max_file_size_mb: int = Field(default=500)
    worker_concurrency: int = Field(default=6)

    # Retrieval
    bm25_refresh_interval_seconds: int = Field(default=300)

    # Observability
    jaeger_endpoint: str = Field(default="http://localhost:4317")
    otel_service_name: str = Field(default="rag-service")

    # Rate limiting
    rate_limit_per_minute: int = Field(default=1000)
    rate_limit_per_ip: int = Field(default=50)

    class Config:
        env_file = ".env"
        case_sensitive = False

    def get_api_keys_list(self) -> list[str]:
        return [k.strip() for k in self.api_keys.split(",") if k.strip()]


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
