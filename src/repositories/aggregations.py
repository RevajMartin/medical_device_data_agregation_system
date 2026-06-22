from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def get_field_stats(
    db: AsyncSession,
    field: str,
    patient_id: str,
    device_type: str,
    start: datetime,
    end: datetime,
) -> dict[str, Any] | None:
    """Avg/min/max/count for one JSON field over a time range. Returns None if no data."""
    row = (
        await db.execute(
            text(
                """
                SELECT
                    AVG((data ->> :field)::float) AS avg,
                    MIN((data ->> :field)::float) AS minimum,
                    MAX((data ->> :field)::float) AS maximum,
                    COUNT(*) AS cnt
                FROM measurements
                WHERE patient_id = :patient_id
                  AND device_type = :device_type
                  AND timestamp BETWEEN :start AND :end
                  AND (data ->> :field) IS NOT NULL
            """
            ),
            {
                "field": field,
                "patient_id": patient_id,
                "device_type": device_type,
                "start": start,
                "end": end,
            },
        )
    ).first()
    if not row or not row.cnt:
        return None
    return {"avg": row.avg, "min": row.minimum, "max": row.maximum, "count": row.cnt}


async def get_total_count(
    db: AsyncSession,
    patient_id: str,
    start: datetime,
    end: datetime,
    device_type: str | None = None,
) -> int:
    sql = (
        "SELECT COUNT(*) AS cnt FROM measurements "
        "WHERE patient_id = :patient_id AND timestamp BETWEEN :start AND :end"
    )
    params: dict[str, Any] = {"patient_id": patient_id, "start": start, "end": end}
    if device_type:
        sql += " AND device_type = :device_type"
        params["device_type"] = device_type
    row = (await db.execute(text(sql), params)).first()
    return int(row.cnt) if row else 0
