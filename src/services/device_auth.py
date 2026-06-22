"""Device authentication — FastAPI dependency."""

import hashlib
import hmac
from typing import Annotated

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from src.config import settings
from src.database import get_db
from src.models.device import Device
from src.repositories import devices as devices_repo

# Registers X-Device-Key as a security scheme in OpenAPI (lock icon in Swagger UI).
# auto_error=False lets us return 401 instead of the default 403 for missing keys.
_api_key_scheme = APIKeyHeader(name="X-Device-Key", auto_error=False)


def hash_api_key(api_key: str) -> str:
    """HMAC-SHA256 of an API key using the application secret.

    Storing HMAC(secret, key) instead of SHA-256(key) means a leaked devices
    table is useless to an attacker who does not also have API_KEY_SECRET —
    they cannot verify any key offline even with the full hash list.
    """
    return hmac.new(
        key=settings.API_KEY_SECRET.encode(),
        msg=api_key.encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()


async def get_authenticated_device(
    api_key: Annotated[str | None, Security(_api_key_scheme)],
    db: AsyncSession = Depends(get_db),
) -> Device:
    """
    Resolve X-Device-Key header to an active Device.

    Raises 401 for a missing or unrecognised key, 403 for a deactivated device.
    Patient/device ID scoping is the caller's responsibility (it depends on the
    request body, which is not available inside a shared dependency).
    """
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    api_key_hash = await run_in_threadpool(hash_api_key, api_key)
    device = await devices_repo.get_by_api_key(db, api_key_hash)

    if device is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not device.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Device is deactivated")

    return device
