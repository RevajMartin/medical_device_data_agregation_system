"""Generic async RabbitMQ consumer (aio-pika) — the worker runtime.

Reads plain-JSON events the outbox relay publishes to the ``medical.events`` topic
exchange, runs the mapped handler, and owns delivery semantics explicitly:

  * **at-least-once**: the message is acked only AFTER the handler succeeds, so a crash
    mid-handler leaves it unacked and RabbitMQ redelivers it;
  * **bounded retry**: a transient failure is retried by re-publishing the event with an
    incremented ``x-attempt`` header (so the count survives redelivery / restart) up to
    ``MAX_RETRIES``;
  * **dead-letter**: the handler records every failure in ``failed_jobs`` (so a poison
    message is never lost); once retries are exhausted the message is acked and left in
    that table for ``/admin`` replay.

Duplicate deliveries are absorbed downstream by per-entity UNIQUE constraints
(``ON CONFLICT DO NOTHING``) -> effectively-once. Concurrency is the QoS prefetch window
(N in-flight coroutines on one event loop) — the aio-pika equivalent of Taskiq's
``--max-async-tasks``.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import aio_pika
from aio_pika.abc import AbstractIncomingMessage

from src.config import settings
from src.messaging import EVENTS_EXCHANGE

logger = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], Awaitable[None]]

MAX_RETRIES = 3


async def run_consumer(queue_name: str, binding_key: str, handler: Handler, prefetch: int) -> None:
    """Consume ``queue_name`` (bound to ``binding_key``) forever, dispatching to ``handler``."""
    connection = await aio_pika.connect_robust(settings.RABBITMQ_URL)
    try:
        # publisher_confirms defaults to True -> re-publish on retry is confirmed.
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=prefetch)

        exchange = await channel.declare_exchange(
            EVENTS_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
        )
        queue = await channel.declare_queue(queue_name, durable=True)
        await queue.bind(exchange, routing_key=binding_key)

        async def on_message(message: AbstractIncomingMessage) -> None:
            attempt = int((message.headers or {}).get("x-attempt", 0))
            try:
                payload = json.loads(message.body)
                await handler(payload)
                await message.ack()
            except Exception:  # noqa: BLE001 - handler already recorded it in failed_jobs
                if attempt + 1 < MAX_RETRIES:
                    logger.warning(
                        "handler failed (attempt %d/%d) on %s; retrying",
                        attempt + 1,
                        MAX_RETRIES,
                        queue_name,
                    )
                    await exchange.publish(
                        aio_pika.Message(
                            body=message.body,
                            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                            content_type="application/json",
                            headers={"x-attempt": attempt + 1},
                        ),
                        routing_key=binding_key,
                    )
                else:
                    logger.error(
                        "giving up on %s after %d attempts; left in failed_jobs for replay",
                        queue_name,
                        attempt + 1,
                    )
                await message.ack()

        await queue.consume(on_message)
        logger.info(
            "Consumer ready: queue=%s key=%s prefetch=%d", queue_name, binding_key, prefetch
        )
        await asyncio.Future()  # run until cancelled (SIGTERM/SIGINT)
    except asyncio.CancelledError:
        logger.info("Consumer %s shutting down...", queue_name)
    finally:
        await connection.close()
