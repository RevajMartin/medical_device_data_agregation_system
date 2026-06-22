from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.risk_score import RiskScore


async def insert_idempotent(
    db: AsyncSession,
    request_id: str,
    patient_id: str,
    score: float,
    level: str,
    scorer_version: str,
    details: dict[str, Any],
) -> bool:
    """Returns True if a new row was created, False if request_id already existed."""
    stmt = (
        pg_insert(RiskScore)
        .values(
            request_id=request_id,
            patient_id=patient_id,
            score=score,
            level=level,
            scorer_version=scorer_version,
            details=details,
        )
        .on_conflict_do_nothing(index_elements=["request_id"])
    )
    result = await db.execute(stmt)
    return result.rowcount > 0


async def list_for_patient(db: AsyncSession, patient_id: str, limit: int = 10) -> list[RiskScore]:
    result = await db.execute(
        select(RiskScore)
        .where(RiskScore.patient_id == patient_id)
        .order_by(desc(RiskScore.created_at))
        .limit(limit)
    )
    return list(result.scalars().all())
