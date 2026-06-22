"""Outbox Relay service - publishes committed outbox events to RabbitMQ.

Reliability model:
  * PostgreSQL LISTEN/NOTIFY for near-instant delivery.
  * Fallback polling every OUTBOX_POLL_INTERVAL seconds as a safety net.
  * Unacked rows are selected with FOR UPDATE SKIP LOCKED inside a transaction so
    multiple relay instances never process the same row, then marked acked=true.

Each row's JSON ``payload`` is published as a plain message to the ``medical.events``
topic exchange with the row's ``topic`` as the routing key (publisher confirms on), so the
broker routes it to the queue bound for that topic. The async consumers (``src/consumers``)
read these plain-JSON messages directly - no task framework. Delivery is at-least-once, so
downstream idempotency (UNIQUE + ON CONFLICT DO NOTHING) makes the effect exactly-once.
"""

import asyncio
import json
import logging

import aio_pika
import asyncpg  # type: ignore[import]

from src.config import settings
from src.messaging import EVENTS_EXCHANGE, KNOWN_TOPICS, QUEUE_BINDINGS

logger = logging.getLogger(__name__)


class OutboxRelay:
    """Transactional Outbox Relay (PostgreSQL NOTIFY/LISTEN + fallback polling)."""

    def __init__(
        self,
        db_url: str = settings.DATABASE_URL,
        poll_interval: float = settings.OUTBOX_POLL_INTERVAL,
        batch_size: int = settings.OUTBOX_BATCH_SIZE,
    ):
        self.db_url = db_url
        self.poll_interval = poll_interval
        self.batch_size = batch_size

        self.pg_conn: asyncpg.Connection | None = None
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        # Strong refs to in-flight NOTIFY handlers so they aren't garbage-collected
        # mid-execution (asyncio only holds weak refs to tasks).
        self._pending: set[asyncio.Task] = set()
        # RabbitMQ publishing side (set up in connect()).
        self.connection: aio_pika.abc.AbstractRobustConnection | None = None
        self.channel: aio_pika.abc.AbstractChannel | None = None
        self.exchange: aio_pika.abc.AbstractExchange | None = None

    async def connect(self, max_retries: int = 30, retry_delay: float = 5.0):
        """Connect to PostgreSQL (with retries), start listening, and start the broker."""
        # postgresql+asyncpg://... -> postgresql://... (asyncpg DSN)
        db_dsn = self.db_url.replace("postgresql+asyncpg://", "postgresql://")
        self._loop = asyncio.get_running_loop()

        for attempt in range(max_retries):
            try:
                self.pg_conn = await asyncpg.connect(db_dsn)
                await self.pg_conn.add_listener("outbox_channel", self._on_notify)
                logger.info("Connected to PostgreSQL and listening on outbox_channel")
                break
            except Exception as e:
                if attempt == max_retries - 1:
                    raise
                logger.warning(
                    f"PostgreSQL connection failed (attempt {attempt + 1}/{max_retries}): {e}"
                )
                await asyncio.sleep(retry_delay)

        # Connect to RabbitMQ and declare the topology (topic exchange + the queues +
        # their bindings) so an event is never published to an unrouted exchange, even
        # if a consumer hasn't started yet - the durable queue holds it. Declarations are
        # idempotent, so the consumers declaring the same topology is harmless.
        self.connection = await aio_pika.connect_robust(settings.RABBITMQ_URL)
        self.channel = await self.connection.channel()
        self.exchange = await self.channel.declare_exchange(
            EVENTS_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
        )
        for queue_name, binding_key in QUEUE_BINDINGS.items():
            queue = await self.channel.declare_queue(queue_name, durable=True)
            await queue.bind(self.exchange, routing_key=binding_key)
        logger.info("Connected to RabbitMQ; exchange '%s' and queues declared", EVENTS_EXCHANGE)

    async def close(self):
        """Close the database connection and the RabbitMQ connection."""
        if self.pg_conn:
            await self.pg_conn.close()
        if self.connection is not None:
            try:
                await self.connection.close()
            except Exception:  # noqa: BLE001 - best-effort shutdown
                pass
        logger.info("Outbox Relay connection closed")

    def _on_notify(self, connection, pid: int, channel: str, payload: str):
        """Synchronous NOTIFY callback - schedules processing on the event loop."""
        if channel == "outbox_channel" and self._loop is not None:
            logger.debug("Received NOTIFY on outbox_channel")
            task = self._loop.create_task(self._process_outbox_guarded())
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)

    async def _process_outbox_guarded(self):
        """Serialize processing so the single asyncpg connection is never used concurrently."""
        async with self._lock:
            try:
                await self._process_outbox()
            except Exception as e:
                logger.error(f"Error processing outbox: {e}")

    async def _process_outbox(self):
        """Process a batch of unacked outbox messages atomically."""
        # The whole batch runs in one transaction so FOR UPDATE SKIP LOCKED holds
        # the row locks until the rows are marked acked and committed.
        async with self.pg_conn.transaction():
            rows = await self.pg_conn.fetch(
                """
                SELECT id, topic, payload
                FROM outbox
                WHERE acked = false
                ORDER BY id ASC
                FOR UPDATE SKIP LOCKED
                LIMIT $1
                """,
                self.batch_size,
            )

            if not rows:
                return

            relayed = 0
            for row in rows:
                # Only ack rows we actually dispatched; an unknown topic is left
                # unacked (not lost) so it can be handled once a task is registered.
                if await self._enqueue(row):
                    await self.pg_conn.execute(
                        "UPDATE outbox SET acked = true WHERE id = $1", row["id"]
                    )
                    relayed += 1

            logger.info(f"Relayed {relayed}/{len(rows)} outbox message(s) to the task queue")

    async def _enqueue(self, row: dict) -> bool:
        """Publish the event payload to RabbitMQ (routing key = topic). Returns True if handled."""
        topic = row["topic"]
        if topic not in KNOWN_TOPICS:
            logger.warning(
                f"No queue bound for outbox topic '{topic}' (id={row['id']}); leaving unacked"
            )
            return False

        # asyncpg returns the JSON column as text; publish the bytes as-is (already JSON).
        payload = row["payload"]
        body = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
        await self.exchange.publish(
            aio_pika.Message(
                body=body,
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                content_type="application/json",
            ),
            routing_key=topic,
        )
        logger.debug(f"Published outbox id={row['id']} (topic={topic})")
        return True

    async def run(self):
        """Main relay loop: process on startup, then poll as a fallback."""
        await self.connect()
        try:
            logger.info("Outbox Relay started - waiting for events...")
            # Drain anything already pending before entering the poll loop.
            await self._process_outbox_guarded()
            while True:
                await asyncio.sleep(self.poll_interval)
                await self._process_outbox_guarded()
        except asyncio.CancelledError:
            logger.info("Outbox Relay shutting down...")
        finally:
            await self.close()


async def run_outbox_relay():
    """Entry point for running the Outbox Relay service."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    relay = OutboxRelay()
    await relay.run()


if __name__ == "__main__":
    asyncio.run(run_outbox_relay())
