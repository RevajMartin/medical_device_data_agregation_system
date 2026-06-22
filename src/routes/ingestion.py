"""Main ingestion endpoint for medical device data."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.models.device import Device
from src.outbox.producer import emit_outbox_event
from src.repositories import measurements as measurements_repo
from src.schemas.measurement import MeasurementRequest, measurement_data
from src.schemas.responses import IngestResponse
from src.services.device_auth import get_authenticated_device

logger = logging.getLogger("medical_data.ingestion")

router = APIRouter(prefix="/ingest", tags=["ingestion"])


@router.post(
    "/",
    response_model=IngestResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {
            "model": IngestResponse,
            "description": "Duplicate — measurement already exists (idempotent)",
        },
        401: {"description": "Missing or invalid X-Device-Key header"},
        403: {"description": "API key not authorised for this patient/device combination"},
    },
)
async def ingest_measurement(
    payload: MeasurementRequest,
    response: Response,
    device: Device = Depends(get_authenticated_device),
    db: AsyncSession = Depends(get_db),
):
    """
    Ingest measurement data from a medical device.

    - Validates device API key (missing/invalid key → 401, deactivated → 403)
    - Physiological validation enforced by Pydantic (out-of-range → 422)
    - Stores measurement idempotently on (device_id, timestamp)
    - Writes to outbox table and triggers PostgreSQL NOTIFY
    - Returns 201 on first insert, 200 on duplicate
    """
    # Payload-level scoping: the dependency verified the key is valid, but the
    # submitted device_id/patient_id must also match the key's owner, and the
    # payload's device_type must match what this device was registered as.
    if device.patient_id != payload.patient_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="API key not authorized for this patient"
        )
    if device.id != payload.device_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="API key not authorized for this device"
        )
    if device.type != payload.device_type:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Device is registered as '{device.type}', cannot submit '{payload.device_type}' data",
        )

    try:
        measurement_id = await measurements_repo.insert_idempotent(
            db,
            device_id=payload.device_id,
            patient_id=payload.patient_id,
            timestamp=payload.timestamp,
            device_type=payload.device_type,
            data=measurement_data(payload),
        )

        if measurement_id is not None:
            await emit_outbox_event(db, "measurement.created", {"measurement_id": measurement_id})
            logger.info(
                "Stored measurement id=%s device=%s type=%s",
                measurement_id,
                payload.device_id,
                payload.device_type,
            )
            response.status_code = status.HTTP_201_CREATED
            return IngestResponse(status="accepted", message="Measurement stored successfully")

        logger.info(
            "Duplicate measurement device=%s ts=%s (idempotent)",
            payload.device_id,
            payload.timestamp,
        )
        response.status_code = status.HTTP_200_OK
        return IngestResponse(status="accepted", message="Measurement already exists (idempotent)")

    except IntegrityError:
        # Defensive net only: insert_idempotent uses ON CONFLICT DO NOTHING, so a
        # unique conflict won't surface here. If anything else aborts the tx we must
        # roll back, otherwise get_db's end-of-request commit would raise.
        await db.rollback()
        logger.warning(
            "IntegrityError on ingest device=%s ts=%s; treated as duplicate",
            payload.device_id,
            payload.timestamp,
        )
        response.status_code = status.HTTP_200_OK
        return IngestResponse(status="accepted", message="Measurement already exists (idempotent)")
