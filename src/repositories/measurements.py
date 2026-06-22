from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.measurement import Measurement


async def insert_idempotent(
    db: AsyncSession,
    device_id: str,
    patient_id: str,
    timestamp: datetime,
    device_type: str,
    data: dict,
) -> int | None:
    """Insert a measurement; returns its id, or None if (device_id, timestamp) already existed."""
    stmt = (
        pg_insert(Measurement)
        .values(
            device_id=device_id,
            patient_id=patient_id,
            timestamp=timestamp,
            device_type=device_type,
            data=data,
        )
        .on_conflict_do_nothing(index_elements=["device_id", "timestamp"])
        .returning(Measurement.id)
    )
    result = await db.execute(stmt)
    row = result.first()
    return row[0] if row else None


async def get_by_id(db: AsyncSession, measurement_id: int) -> Measurement | None:
    return await db.get(Measurement, measurement_id)


async def get_windowed(
    db: AsyncSession, patient_id: str, since: datetime, limit: int
) -> list[Measurement]:
    result = await db.execute(
        select(Measurement)
        .where(Measurement.patient_id == patient_id, Measurement.timestamp >= since)
        .order_by(Measurement.timestamp.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
