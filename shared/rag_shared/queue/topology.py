"""
RabbitMQ topology declaration.

Exchange / Queue / Binding layout
──────────────────────────────────
Exchanges:
  rag.ingestion     (direct)    – routes ingest tasks by priority key
  rag.embedding     (direct)    – routes embedding tasks
  rag.ocr           (direct)    – routes OCR tasks
  rag.priority      (headers)   – priority routing via headers x-priority

Dead-letter exchanges:
  rag.ingestion.dlx (direct)
  rag.embedding.dlx (direct)
  rag.ocr.dlx       (direct)

Queues (quorum type for HA / exactly-once semantics):
  rag.ingestion.queue        → bound to rag.ingestion, rk="ingest"
  rag.embedding.queue        → bound to rag.embedding, rk="embed"
  rag.ocr.queue              → bound to rag.ocr,       rk="ocr"

  rag.ingestion.dlq          → bound to rag.ingestion.dlx, rk="ingest"
  rag.embedding.dlq          → bound to rag.embedding.dlx, rk="embed"
  rag.ocr.dlq                → bound to rag.ocr.dlx,       rk="ocr"
"""

import aio_pika
import logging

logger = logging.getLogger(__name__)

# ─── Exchange names ────────────────────────────────────────────────────────────
EXCHANGE_INGESTION = "rag.ingestion"
EXCHANGE_EMBEDDING = "rag.embedding"
EXCHANGE_OCR = "rag.ocr"
EXCHANGE_PRIORITY = "rag.priority"

EXCHANGE_INGESTION_DLX = "rag.ingestion.dlx"
EXCHANGE_EMBEDDING_DLX = "rag.embedding.dlx"
EXCHANGE_OCR_DLX = "rag.ocr.dlx"

# ─── Queue names ───────────────────────────────────────────────────────────────
QUEUE_INGESTION = "rag.ingestion.queue"
QUEUE_EMBEDDING = "rag.embedding.queue"
QUEUE_OCR = "rag.ocr.queue"

QUEUE_INGESTION_DLQ = "rag.ingestion.dlq"
QUEUE_EMBEDDING_DLQ = "rag.embedding.dlq"
QUEUE_OCR_DLQ = "rag.ocr.dlq"

# ─── Routing keys ──────────────────────────────────────────────────────────────
RK_INGEST = "ingest"
RK_EMBED = "embed"
RK_OCR = "ocr"

# Quorum queue argument applied to all primary + dead-letter queues
_QUORUM_ARGS = {"x-queue-type": "quorum"}


async def declare_topology(channel: aio_pika.Channel) -> None:  # noqa: C901
    """
    Idempotently declare all exchanges, queues and bindings.

    Call once during service startup *before* publishing or consuming.
    Safe to call multiple times; all declarations use active=False so an
    existing topology is simply verified rather than recreated.
    """

    # ── Dead-letter exchanges (declared first so primary queue args reference them) ──
    dlx_ingestion = await channel.declare_exchange(
        EXCHANGE_INGESTION_DLX,
        type=aio_pika.ExchangeType.DIRECT,
        durable=True,
        passive=False,
    )
    dlx_embedding = await channel.declare_exchange(
        EXCHANGE_EMBEDDING_DLX,
        type=aio_pika.ExchangeType.DIRECT,
        durable=True,
        passive=False,
    )
    dlx_ocr = await channel.declare_exchange(
        EXCHANGE_OCR_DLX,
        type=aio_pika.ExchangeType.DIRECT,
        durable=True,
        passive=False,
    )

    # ── Primary exchanges ──────────────────────────────────────────────────────
    ex_ingestion = await channel.declare_exchange(
        EXCHANGE_INGESTION,
        type=aio_pika.ExchangeType.DIRECT,
        durable=True,
        passive=False,
    )
    ex_embedding = await channel.declare_exchange(
        EXCHANGE_EMBEDDING,
        type=aio_pika.ExchangeType.DIRECT,
        durable=True,
        passive=False,
    )
    ex_ocr = await channel.declare_exchange(
        EXCHANGE_OCR,
        type=aio_pika.ExchangeType.DIRECT,
        durable=True,
        passive=False,
    )

    # Headers exchange for priority-based routing
    await channel.declare_exchange(
        EXCHANGE_PRIORITY,
        type=aio_pika.ExchangeType.HEADERS,
        durable=True,
        passive=False,
    )

    # ── Dead-letter queues (must exist before primary queues reference them) ───
    dlq_ingestion = await channel.declare_queue(
        QUEUE_INGESTION_DLQ,
        durable=True,
        arguments=_QUORUM_ARGS,
    )
    dlq_embedding = await channel.declare_queue(
        QUEUE_EMBEDDING_DLQ,
        durable=True,
        arguments=_QUORUM_ARGS,
    )
    dlq_ocr = await channel.declare_queue(
        QUEUE_OCR_DLQ,
        durable=True,
        arguments=_QUORUM_ARGS,
    )

    await dlq_ingestion.bind(dlx_ingestion, routing_key=RK_INGEST)
    await dlq_embedding.bind(dlx_embedding, routing_key=RK_EMBED)
    await dlq_ocr.bind(dlx_ocr, routing_key=RK_OCR)

    # ── Primary queues with DLX / DLK arguments ────────────────────────────────
    q_ingestion = await channel.declare_queue(
        QUEUE_INGESTION,
        durable=True,
        arguments={
            **_QUORUM_ARGS,
            "x-dead-letter-exchange": EXCHANGE_INGESTION_DLX,
            "x-dead-letter-routing-key": RK_INGEST,
            # Requeue limit before a message is sent to the DLQ
            "x-delivery-limit": 5,
        },
    )
    q_embedding = await channel.declare_queue(
        QUEUE_EMBEDDING,
        durable=True,
        arguments={
            **_QUORUM_ARGS,
            "x-dead-letter-exchange": EXCHANGE_EMBEDDING_DLX,
            "x-dead-letter-routing-key": RK_EMBED,
            "x-delivery-limit": 3,
        },
    )
    q_ocr = await channel.declare_queue(
        QUEUE_OCR,
        durable=True,
        arguments={
            **_QUORUM_ARGS,
            "x-dead-letter-exchange": EXCHANGE_OCR_DLX,
            "x-dead-letter-routing-key": RK_OCR,
            "x-delivery-limit": 3,
        },
    )

    # ── Bindings ───────────────────────────────────────────────────────────────
    await q_ingestion.bind(ex_ingestion, routing_key=RK_INGEST)
    await q_embedding.bind(ex_embedding, routing_key=RK_EMBED)
    await q_ocr.bind(ex_ocr, routing_key=RK_OCR)

    logger.info(
        "RabbitMQ topology declared: "
        "exchanges=%s, queues=%s",
        [
            EXCHANGE_INGESTION, EXCHANGE_EMBEDDING, EXCHANGE_OCR, EXCHANGE_PRIORITY,
            EXCHANGE_INGESTION_DLX, EXCHANGE_EMBEDDING_DLX, EXCHANGE_OCR_DLX,
        ],
        [
            QUEUE_INGESTION, QUEUE_EMBEDDING, QUEUE_OCR,
            QUEUE_INGESTION_DLQ, QUEUE_EMBEDDING_DLQ, QUEUE_OCR_DLQ,
        ],
    )


# Alias used by some services
ensure_topology = declare_topology
