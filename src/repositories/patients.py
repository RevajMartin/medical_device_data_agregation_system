from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.device import Patient


async def upsert(db: AsyncSession, patient_id: str) -> None:
    """Ensure a patient row exists; no-op if already present."""
    stmt = (
        pg_insert(Patient)
        .values(id=patient_id, name=patient_id)
        .on_conflict_do_nothing(index_elements=["id"])
    )
    await db.execute(stmt)
