import logging
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from src.database import task_session
from src.models.failed_job import FailedJob

logger = logging.getLogger(__name__)


async def upsert_failure(
    task_name: str, topic: str, dedup_key: str, payload: dict[str, Any], error: str
) -> None:
    """Upsert a dead-letter record in its own short-lived, loop-safe session."""
    err = error[:4000]
    async with task_session() as db:
        stmt = (
            pg_insert(FailedJob)
            .values(
                task_name=task_name, topic=topic, dedup_key=dedup_key, payload=payload, error=err
            )
            .on_conflict_do_update(
                index_elements=["task_name", "dedup_key"],
                set_={"error": err, "attempts": FailedJob.attempts + 1, "updated_at": func.now()},
            )
        )
        await db.execute(stmt)
        await db.commit()
    logger.error(f"DLQ: recorded failed {task_name} (dedup={dedup_key}): {err[:200]}")


async def clear_success(db: AsyncSession, task_name: str, dedup_key: str) -> None:
    """Remove the dead-letter record on success, within the caller's transaction."""
    await db.execute(
        delete(FailedJob).where(FailedJob.task_name == task_name, FailedJob.dedup_key == dedup_key)
    )


async def list_all(db: AsyncSession) -> list[FailedJob]:
    result = await db.execute(select(FailedJob).order_by(FailedJob.updated_at.desc()))
    return list(result.scalars().all())


async def get_by_id(db: AsyncSession, job_id: int) -> FailedJob | None:
    return await db.get(FailedJob, job_id)
