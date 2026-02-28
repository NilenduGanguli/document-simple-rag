from opentelemetry import context as otel_context, trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.propagate import inject, extract
import logging

logger = logging.getLogger(__name__)


def configure_tracer(service_name: str, endpoint: str = "http://localhost:4317") -> trace.Tracer:
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    try:
        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
        processor = BatchSpanProcessor(
            exporter,
            max_queue_size=2048,
            max_export_batch_size=512,
            export_timeout_millis=10_000,
        )
        provider.add_span_processor(processor)
        logger.info(f"OpenTelemetry tracing configured for {service_name} -> {endpoint}")
    except Exception as e:
        logger.warning(f"Failed to configure OTLP exporter: {e}. Tracing disabled.")

    trace.set_tracer_provider(provider)

    # Auto-instrument asyncpg if available
    try:
        from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
        AsyncPGInstrumentor().instrument()
    except Exception:
        pass

    return trace.get_tracer(service_name)


def inject_trace_context() -> dict:
    """Inject current trace context into a dict for use as AMQP message headers."""
    carrier: dict[str, str] = {}
    inject(carrier)
    return carrier


def extract_trace_context(headers: dict | None):
    """Extract trace context from AMQP message headers.

    Returns a context object that can be used with
    ``otel_context.attach(ctx)`` to make spans children of the
    upstream trace.  Returns ``None`` if *headers* is empty.
    """
    if not headers:
        return None
    return extract(headers)
