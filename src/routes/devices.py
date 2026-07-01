"""Device registration and authentication routes."""

from secrets import token_hex

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from src.config import settings
from src.database import get_db
from src.repositories import devices as devices_repo
from src.repositories import patients as patients_repo
from src.schemas.device import DeviceRegister, DeviceRegisterResponse
from src.services.device_auth import hash_api_key, require_admin

router = APIRouter(prefix="/devices", tags=["devices"])


@router.post(
    "/register",
    response_model=DeviceRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def register_device(
    payload: DeviceRegister,
    db: AsyncSession = Depends(get_db),
):
    """
    Register a new medical device and generate an API key.

    The API key is scoped to a patient_id - the device can only submit data for that patient.
    """
    api_key = token_hex(settings.API_KEY_LENGTH)
    api_key_hash = await run_in_threadpool(hash_api_key, api_key)

    await patients_repo.upsert(db, payload.patient_id)

    if not await devices_repo.insert_if_new(
        db, payload.device_id, payload.patient_id, payload.device_type, api_key_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Device {payload.device_id} already registered",
        )

    return DeviceRegisterResponse(
        device_id=payload.device_id,
        patient_id=payload.patient_id,
        api_key=api_key,
    )
