from datetime import datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.alert import Alert


async def insert_idempotent(
    db: AsyncSession,
    measurement_id: int,
    patient_id: str,
    device_type: str,
    field: str,
    value: float,
    rule: str,
    severity: str,
    timestamp: datetime,
) -> bool:
    """Returns True if a new alert row was created, False if UNIQUE(measurement_id, rule) conflicted."""
    stmt = (
        pg_insert(Alert)
        .values(
            measurement_id=measurement_id,
            patient_id=patient_id,
            device_type=device_type,
            field=field,
            value=value,
            rule=rule,
            severity=severity,
            timestamp=timestamp,
        )
        .on_conflict_do_nothing(index_elements=["measurement_id", "rule"])
    )
    result = await db.execute(stmt)
    return result.rowcount > 0
