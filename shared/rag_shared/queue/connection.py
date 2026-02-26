import aio_pika
import logging

logger = logging.getLogger(__name__)


async def get_rabbit_connection(url: str) -> aio_pika.RobustConnection:
    """Get a robust connection that auto-reconnects."""
    connection = await aio_pika.connect_robust(
        url,
        reconnect_interval=5,
        fail_fast=False,
    )
    logger.info("RabbitMQ connection established")
    return connection


async def get_channel(connection: aio_pika.RobustConnection) -> aio_pika.Channel:
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=1)
    return channel
