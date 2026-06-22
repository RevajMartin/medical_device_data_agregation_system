
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.device import Device


async def get_by_api_key(db: AsyncSession, api_key_hash: str) -> Device | None:
    result = await db.execute(select(Device).where(Device.api_key_hash == api_key_hash))
    return result.scalar_one_or_none()


async def insert_if_new(
    db: AsyncSession,
    device_id: str,
    patient_id: str,
    device_type: str,
    api_key_hash: str,
) -> bool:
    """Insert the device; returns True if created, False if device_id already existed."""
    stmt = (
        pg_insert(Device)
        .values(
            id=device_id,
            patient_id=patient_id,
            type=device_type,
            api_key_hash=api_key_hash,
            is_active=True,
        )
        .on_conflict_do_nothing(index_elements=["id"])
    )
    result = await db.execute(stmt)
    return result.rowcount > 0
