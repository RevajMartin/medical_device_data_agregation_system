"""
Tests for the risk-scoring background job - a NEW job type added on top of the
pluggable outbox architecture (new topic `riskscore.requested` -> compute_risk_score).
The scorer is a rule-based heuristic (not ML).

Verifies the full vertical slice end to end against the live stack, plus idempotency
of the new job under redelivery (UNIQUE(request_id)).
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx

from tests.helpers import BASE_URL, PATIENT_1, db_execute, db_fetch, ingest_measurement, wait_for


async def test_risk_score_pipeline(registered):
    """Request a risk score -> worker computes from recent vitals -> stored + queryable."""
    base = datetime.now(UTC)
    # Elevated but VALID vitals (within validation bounds) -> non-trivial risk score.
    await ingest_measurement(
        "HR001",
        PATIENT_1,
        {"device_type": "heart_rate", "heart_rate": 140, "measurement_quality": "good"},
        registered["HR001"],
        base,
    )
    await ingest_measurement(
        "BP001",
        PATIENT_1,
        {"device_type": "blood_pressure", "systolic": 175, "diastolic": 95, "pulse": 120},
        registered["BP001"],
        base + timedelta(seconds=1),
    )
    await ingest_measurement(
        "PO001",
        PATIENT_1,
        {"device_type": "pulse_oximeter", "spo2": 91.0, "perfusion_index": 2.0},
        registered["PO001"],
        base + timedelta(seconds=2),
    )

    # Request the async ML risk score.
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{BASE_URL}/patients/{PATIENT_1}/risk-score")
    assert resp.status_code == 202
    request_id = resp.json()["request_id"]

    # Worker computes and stores it asynchronously.
    async def scored():
        rows = await db_fetch(
            "SELECT request_id FROM risk_scores WHERE request_id = $1", request_id
        )
        return len(rows) == 1

    assert await wait_for(scored, timeout=15.0), "risk score was not computed in time"

    rows = await db_fetch("SELECT * FROM risk_scores WHERE request_id = $1", request_id)
    row = rows[0]
    assert row["patient_id"] == PATIENT_1
    assert row["scorer_version"] == "rules-v0"
    assert row["level"] in ("low", "medium", "high")
    # hr_mean=140, spo2_min=91, systolic_max=175 -> 24 + 12 + 22 = 58.0 (medium)
    assert row["level"] == "medium"
    assert 50 <= row["score"] <= 65
    # asyncpg returns a JSON column as text; parse it.
    details = row["details"] if isinstance(row["details"], dict) else json.loads(row["details"])
    assert {"hr_mean", "spo2_min", "systolic_max"} <= set(details.keys())

    # Result is exposed via GET.
    async with httpx.AsyncClient() as client:
        get_resp = await client.get(f"{BASE_URL}/patients/{PATIENT_1}/risk-scores")
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert any(s["request_id"] == request_id for s in body["risk_scores"])


async def test_risk_score_idempotent_on_redelivery(registered):
    """Re-delivering the same job (same request_id) must yield exactly one row.

    Simulates at-least-once redelivery through the real path: two identical outbox
    events -> relay enqueues twice -> worker runs twice -> UNIQUE(request_id) keeps it
    to a single row.
    """
    request_id = "fixed-" + uuid.uuid4().hex
    payload = json.dumps({"patient_id": PATIENT_1, "request_id": request_id})
    for _ in range(2):
        await db_execute(
            "INSERT INTO outbox (topic, payload, acked) VALUES ($1, $2, false)",
            "riskscore.requested",
            payload,
        )
    await db_execute("NOTIFY outbox_channel, 'redelivery_test'")

    async def at_least_one():
        rows = await db_fetch(
            "SELECT request_id FROM risk_scores WHERE request_id = $1", request_id
        )
        return len(rows) >= 1

    assert await wait_for(at_least_one, timeout=15.0)
    await asyncio.sleep(2)  # give the second delivery time to be processed too

    rows = await db_fetch("SELECT COUNT(*) AS c FROM risk_scores WHERE request_id = $1", request_id)
    assert rows[0]["c"] == 1
