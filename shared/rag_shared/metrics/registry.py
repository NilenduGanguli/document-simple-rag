from prometheus_client import Counter, Gauge, Histogram, REGISTRY
from prometheus_client import make_asgi_app


# Guard against duplicate registration in tests
def _register_or_get(metric_class, name, documentation, *args, **kwargs):
    try:
        return metric_class(name, documentation, *args, **kwargs)
    except ValueError:
        return REGISTRY._names_to_collectors.get(name)


ingest_documents_total = Counter(
    'rag_ingest_documents_total',
    'Total documents submitted for ingestion',
    ['status'],  # success, duplicate, failed, invalid
)

queue_depth = Gauge(
    'rag_queue_depth',
    'Current messages in RabbitMQ queue',
    ['queue_name'],
)

retrieval_latency_ms = Histogram(
    'rag_retrieval_latency_ms',
    'Full retrieval pipeline latency in milliseconds',
    buckets=[50, 100, 200, 400, 800, 1600, 3200, 6400],
)

onnx_inference_duration_ms = Histogram(
    'rag_onnx_inference_duration_ms',
    'ONNX Runtime inference duration in milliseconds',
    ['model_type'],
    buckets=[50, 100, 200, 500, 1000, 2000, 5000],
)

onnx_pool_wait_ms = Histogram(
    'rag_onnx_pool_wait_ms',
    'Time waiting for ONNX session pool slot in milliseconds',
    ['model_type'],
    buckets=[10, 50, 100, 200, 500, 1000],
)

embedding_batch_duration_ms = Histogram(
    'rag_embedding_batch_duration_ms',
    'Embedding batch processing duration in milliseconds',
    buckets=[100, 500, 1000, 2000, 5000],
)

cache_hit_ratio = Gauge(
    'rag_cache_hit_ratio',
    'Redis cache hit ratio',
    ['cache_type'],
)

dense_search_ms = Histogram(
    'rag_dense_search_ms',
    'Dense vector search duration in milliseconds',
    buckets=[5, 10, 20, 50, 100, 200, 500],
)

ocp_pod_cpu_throttling = Gauge(
    'rag_ocp_pod_cpu_throttling_ratio',
    'CPU throttling ratio for the pod',
)


def get_metrics_app():
    """Returns ASGI app for /metrics endpoint."""
    return make_asgi_app()
