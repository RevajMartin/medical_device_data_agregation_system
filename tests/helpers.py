"""Shared helpers for the integration test suite.

These tests run against the live stack (``docker compose up``): the API on
localhost:8000 and PostgreSQL on localhost:5432.
"""

import asyncio
import time
from datetime import UTC, datetime

import asyncpg
import httpx

BASE_URL = "http://localhost:8000"
DB_DSN = "postgresql://user:pass@localhost:5432/medical_data"

PATIENT_1 = "patient_001"
PATIENT_2 = "patient_002"

# 3 device types x 2 patients
DEVICES = {
    "HR001": {"patient_id": PATIENT_1, "device_type": "heart_rate"},
    "HR002": {"patient_id": PATIENT_2, "device_type": "heart_rate"},
    "BP001": {"patient_id": PATIENT_1, "device_type": "blood_pressure"},
    "BP002": {"patient_id": PATIENT_2, "device_type": "blood_pressure"},
    "PO001": {"patient_id": PATIENT_1, "device_type": "pulse_oximeter"},
    "PO002": {"patient_id": PATIENT_2, "device_type": "pulse_oximeter"},
}

DATA_TABLES = ["alerts", "outbox", "measurements", "devices", "patients"]


async def _wait_outbox_drained(timeout: float = 10.0) -> None:
    """Wait until the relay has dispatched all events (no unacked outbox rows).

    This lets the background worker/relay settle before we TRUNCATE, drastically
    reducing lock contention against their in-flight transactions.
    """
    conn = await asyncpg.connect(DB_DSN)
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            pending = await conn.fetchval("SELECT count(*) FROM outbox WHERE acked = false")
            if not pending:
                return
            await asyncio.sleep(0.25)
    finally:
        await conn.close()


async def truncate_all() -> None:
    """Wipe all data tables for an isolated, repeatable test.

    The consumers / outbox relay run continuously, so a TRUNCATE (which needs
    AccessExclusiveLock) can deadlock against their row-level writes. We first wait
    for the outbox to drain, then retry on transient lock/deadlock errors.
    """
    await _wait_outbox_drained()

    last_exc: Exception | None = None
    for _ in range(15):
        conn = await asyncpg.connect(DB_DSN)
        try:
            await conn.execute("SET lock_timeout = '2s'")
            await conn.execute(f"TRUNCATE {', '.join(DATA_TABLES)} RESTART IDENTITY CASCADE")
            return
        except (
            asyncpg.exceptions.DeadlockDetectedError,
            asyncpg.exceptions.LockNotAvailableError,
        ) as exc:
            last_exc = exc
            await asyncio.sleep(0.4)
        finally:
            await conn.close()

    raise RuntimeError("truncate_all failed after retries") from last_exc


async def register_device(device_id: str, patient_id: str, device_type: str) -> str:
    """Register a device and return its API key (raises on non-2xx)."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{BASE_URL}/devices/register",
            json={
                "device_id": device_id,
                "patient_id": patient_id,
                "device_type": device_type,
            },
        )
        resp.raise_for_status()
        return resp.json()["api_key"]


async def ingest_measurement(
    device_id: str,
    patient_id: str,
    data: dict,
    api_key: str | None,
    timestamp: datetime | None = None,
) -> httpx.Response:
    """Ingest a measurement. Pass api_key=None to omit the X-Device-Key header."""
    # Flat payload: envelope fields + the device-specific fields (incl. device_type),
    # matching the API's top-level discriminated union.
    payload = {
        "device_id": device_id,
        "patient_id": patient_id,
        "timestamp": (timestamp or datetime.now(UTC)).isoformat(),
        **data,
    }
    headers = {"X-Device-Key": api_key} if api_key is not None else {}
    async with httpx.AsyncClient() as client:
        return await client.post(f"{BASE_URL}/ingest/", json=payload, headers=headers)


async def get_aggregations(patient_id: str, start: datetime, end: datetime) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BASE_URL}/aggregations/{patient_id}",
            params={"start": start.isoformat(), "end": end.isoformat()},
        )
        resp.raise_for_status()
        return resp.json()


async def db_fetch(query: str, *args) -> list[dict]:
    """Run a parameterized query ($1, $2, ...) and return rows as dicts."""
    conn = await asyncpg.connect(DB_DSN)
    try:
        rows = await conn.fetch(query, *args)
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def db_execute(query: str, *args) -> str:
    """Run a parameterized statement ($1, $2, ...) and return its status."""
    conn = await asyncpg.connect(DB_DSN)
    try:
        return await conn.execute(query, *args)
    finally:
        await conn.close()


async def wait_for(predicate, timeout: float = 10.0, interval: float = 0.5) -> bool:
    """Poll an async predicate until it returns truthy or the timeout elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await predicate():
            return True
        await asyncio.sleep(interval)
    return False


# --- Reliability invariants (shared oracle for the load / chaos tests) ---


async def assert_no_duplicate_measurements() -> None:
    dups = await db_fetch(
        "SELECT device_id, timestamp FROM measurements "
        "GROUP BY device_id, timestamp HAVING COUNT(*) > 1"
    )
    assert dups == [], f"duplicate measurements: {dups}"


async def assert_no_duplicate_alerts() -> None:
    dups = await db_fetch(
        "SELECT measurement_id, rule FROM alerts "
        "GROUP BY measurement_id, rule HAVING COUNT(*) > 1"
    )
    assert dups == [], f"duplicate alerts: {dups}"


async def assert_outbox_drained(timeout: float = 20.0) -> None:
    async def drained():
        rows = await db_fetch("SELECT COUNT(*) AS c FROM outbox WHERE acked = false")
        return rows[0]["c"] == 0

    assert await wait_for(drained, timeout=timeout), "outbox not fully drained"
