"""Aggregation endpoints for querying measurement data."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.repositories import aggregations as aggregations_repo
from src.schemas.responses import AggregationResponse, FieldStats
from src.services.device_auth import require_patient_scope

# Patient-scoped: a valid X-Device-Key whose patient matches {patient_id} (else 401/403).
router = APIRouter(
    prefix="/aggregations",
    tags=["aggregations"],
    dependencies=[Depends(require_patient_scope)],
    responses={
        401: {"description": "Missing or invalid X-Device-Key"},
        403: {"description": "API key not authorized for this patient"},
    },
)


# Numeric measurement fields grouped by their owning device_type. Field names come
# from this fixed whitelist (never from user input) so they are safe to use as the
# JSON key, and the key itself is still passed as a bound parameter.
FIELD_DEVICE = {
    "heart_rate": "heart_rate",
    "systolic": "blood_pressure",
    "diastolic": "blood_pressure",
    "pulse": "blood_pressure",
    "spo2": "pulse_oximeter",
    "perfusion_index": "pulse_oximeter",
}


@router.get("/{patient_id}", response_model=AggregationResponse, response_model_exclude_none=True)
async def get_patient_aggregations(
    patient_id: str,
    start: datetime | None = None,
    end: datetime | None = None,
    device_type: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Get aggregated metrics (avg/min/max/count) for a patient over a time range,
    grouped per measurement field.

    Query params: ``start``, ``end`` (ISO 8601, tz-aware), optional ``device_type``.
    Aggregations are computed in SQL.
    """
    if end is None:
        end = datetime.now(UTC)
    if start is None:
        start = end - timedelta(days=1)

    if start > end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start must be before end",
        )

    field_stats: dict = {}
    for field, field_device in FIELD_DEVICE.items():
        if device_type and device_type != field_device:
            continue
        stats = await aggregations_repo.get_field_stats(
            db, field, patient_id, field_device, start, end
        )
        if stats:
            field_stats[field] = FieldStats(**stats)

    total_count = await aggregations_repo.get_total_count(db, patient_id, start, end, device_type)
    return AggregationResponse(
        patient_id=patient_id,
        start=start,
        end=end,
        total_count=total_count,
        **field_stats,
    )
