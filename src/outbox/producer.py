"""Transactional outbox producer.

A single, reusable place to emit background-job events. Any endpoint/service can drop
an event into the outbox within its own DB transaction; the relay then dispatches it to
the worker.

Adding a new background job is therefore:
  1. write the task (worker side),
  2. register its topic -> task in the relay's ``TOPIC_TASKS`` (src/outbox/relay.py),
  3. call ``emit_outbox_event(db, "<topic>", {...})`` from the producer.
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.outbox import Outbox


async def emit_outbox_event(db: AsyncSession, topic: str, payload: dict[str, Any]) -> None:
    """
    Insert an outbox event and signal the relay, in the caller's transaction.

    The row and the NOTIFY commit atomically with the caller's business write (the
    ``get_db`` dependency commits at the end of the request), so the event is published
    only if that write succeeds (transactional outbox). The JSON ``payload`` is
    serialized by SQLAlchemy's JSON column type.
    """
    db.add(Outbox(topic=topic, payload=payload, acked=False))
    await db.flush()  # issue the INSERT within this transaction
    await db.execute(text("NOTIFY outbox_channel, 'new_message'"))
