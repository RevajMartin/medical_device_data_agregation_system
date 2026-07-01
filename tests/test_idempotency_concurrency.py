"""Concurrency-level idempotency tests (run against the live stack).

Complements ``test_load_scenario.py::test_idempotency`` (which is sequential) by
exercising the guarantee the spec calls out explicitly — idempotency under
*concurrent* duplicate submissions — plus that a duplicate / a redelivered task
never double-emits an event or double-writes a row.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta

from tests.helpers import (
    PATIENT_1,
    db_execute,
    db_fetch,
    ingest_measurement,
    measurement_id,
    wait_for,
)

HR = {"device_type": "heart_rate", "heart_rate": 72, "measurement_quality": "good"}
HR_CLINICAL = {"device_type": "heart_rate", "heart_rate": 165, "measurement_quality": "good"}


async def _event_count(measurement_id: int) -> int:
    rows = await db_fetch(
        "SELECT COUNT(*) AS c FROM outbox "
        "WHERE topic = 'measurement.created' AND (payload->>'measurement_id')::bigint = $1",
        measurement_id,
    )
    return rows[0]["c"]


async def test_concurrent_duplicate_ingest(registered):
    """20 identical (device_id, timestamp) submissions fired simultaneously: exactly
    one 201 (the winner), the rest 200, exactly one row, exactly one outbox event."""
    ts = datetime.now(UTC) - timedelta(hours=5)

    responses = await asyncio.gather(
        *[ingest_measurement("HR001", PATIENT_1, HR, registered["HR001"], ts) for _ in range(20)]
    )
    codes = [r.status_code for r in responses]
    assert all(c in (200, 201) for c in codes), codes
    assert codes.count(201) == 1, f"expected exactly one 201 winner, got {codes.count(201)}"

    rows = await db_fetch(
        "SELECT id FROM measurements WHERE device_id = $1 AND timestamp = $2", "HR001", ts
    )
    assert len(rows) == 1, "concurrent duplicates must yield exactly one row"

    # Only the winning insert emits an event, so the background pipeline runs once.
    assert await _event_count(rows[0]["id"]) == 1


async def test_duplicate_ingest_emits_no_second_event(registered):
    """A duplicate submission (HTTP 200) must not enqueue a second background job."""
    ts = datetime.now(UTC) - timedelta(hours=6)

    r1 = await ingest_measurement("HR001", PATIENT_1, HR, registered["HR001"], ts)
    assert r1.status_code == 201
    mid = await measurement_id("HR001", ts)

    r2 = await ingest_measurement("HR001", PATIENT_1, HR, registered["HR001"], ts)
    assert r2.status_code == 200

    assert await _event_count(mid) == 1, "duplicate must not emit a second outbox event"


async def test_alert_idempotent_on_redelivery(registered):
    """Re-delivering measurement.created for the same measurement yields one alert
    (UNIQUE(measurement_id, rule)) — the at-least-once -> effectively-once guarantee."""
    ts = datetime.now(UTC) - timedelta(hours=7)

    r = await ingest_measurement("HR001", PATIENT_1, HR_CLINICAL, registered["HR001"], ts)
    assert r.status_code == 201
    mid = await measurement_id("HR001", ts)

    async def alert_created():
        rows = await db_fetch("SELECT id FROM alerts WHERE measurement_id = $1", mid)
        return len(rows) == 1

    assert await wait_for(alert_created, timeout=15.0), "first alert was not created"

    # Simulate broker at-least-once redelivery: emit the same event again.
    await db_execute(
        "INSERT INTO outbox (topic, payload, acked) VALUES ($1, $2, false)",
        "measurement.created",
        json.dumps({"measurement_id": mid}),
    )
    await db_execute("NOTIFY outbox_channel, 'alert_redelivery'")
    await asyncio.sleep(3)  # allow the redelivered job to run

    rows = await db_fetch("SELECT COUNT(*) AS c FROM alerts WHERE measurement_id = $1", mid)
    assert rows[0]["c"] == 1, "redelivery must not create a duplicate alert"
