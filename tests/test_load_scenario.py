"""
Integration / load scenario for the Medical Device Data Aggregation System.

Runs against the live stack (docker compose up). Every test is isolated: the
``clean_db`` autouse fixture truncates all tables first, and devices are provided
by the ``registered`` fixture.

Behaviour follows the SPECIFICATION (task_spec): out-of-range values are rejected
with 422 (see tests/test_contracts.py), and clinical alert thresholds are
heart_rate > 150, spo2 < 90, systolic > 180.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx

from tests.helpers import (
    ADMIN_TOKEN,
    BASE_URL,
    DEVICES,
    PATIENT_1,
    db_fetch,
    get_aggregations,
    hr_reading,
    ingest_measurement,
    register_device,
    wait_for_alert_count,
    wait_for_measurement_count,
)

# ---------------------------------------------------------------------------
# Device registration
# ---------------------------------------------------------------------------


async def test_register_devices(clean_db):
    """Register all 6 devices; duplicate registration must return 409."""
    keys = {}
    for device_id, cfg in DEVICES.items():
        api_key = await register_device(device_id, cfg["patient_id"], cfg["device_type"])
        assert api_key is not None
        assert len(api_key) == 64  # sha256 hex source key length
        keys[device_id] = api_key

    assert len(set(keys.values())) == 6  # all keys unique

    # Re-registering an existing device -> 409 Conflict
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{BASE_URL}/devices/register",
            json={"device_id": "HR001", "patient_id": PATIENT_1, "device_type": "heart_rate"},
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Normal ingestion
# ---------------------------------------------------------------------------


async def test_ingest_normal_measurements(registered):
    """Ingest 60 in-range measurements (10 per device) concurrently."""
    base = datetime.now(UTC)
    measurements = []
    for i in range(10):
        for idx, (device_id, cfg) in enumerate(DEVICES.items()):
            ts = base + timedelta(seconds=i * 6 + idx)
            if cfg["device_type"] == "heart_rate":
                data = {
                    "device_type": "heart_rate",
                    "heart_rate": 60 + (i % 40),
                    "measurement_quality": "good",
                }
            elif cfg["device_type"] == "blood_pressure":
                data = {
                    "device_type": "blood_pressure",
                    "systolic": 90 + (i % 60),
                    "diastolic": 60 + (i % 30),
                    "pulse": 60 + (i % 40),
                }
            else:
                data = {
                    "device_type": "pulse_oximeter",
                    "spo2": 95.0 + (i % 5),
                    "perfusion_index": 2.0 + (i % 5),
                }
            measurements.append((device_id, cfg["patient_id"], data, registered[device_id], ts))

    responses = await asyncio.gather(
        *[ingest_measurement(d, p, data, key, ts) for d, p, data, key, ts in measurements]
    )
    for r in responses:
        assert r.status_code in (200, 201)

    # The API commits in its request-teardown (after the 201 is returned), so a few
    # commits can still be in flight right after gather() returns; poll for the count.
    assert await wait_for_measurement_count(
        60, timeout=10.0
    ), "not all 60 measurements were committed"


# ---------------------------------------------------------------------------
# Clinical threshold alerts (full outbox -> relay -> consumer -> alert pipeline)
# ---------------------------------------------------------------------------


async def test_clinical_threshold_alerts(registered):
    """Clinically concerning (but valid) values must asynchronously create alerts."""
    base = datetime.now(UTC) - timedelta(hours=1)
    cases = [
        (
            "HR001",
            PATIENT_1,
            hr_reading(160),
            {"field": "heart_rate", "rule": "heart_rate>150"},
        ),
        (
            "PO001",
            PATIENT_1,
            {"device_type": "pulse_oximeter", "spo2": 85.0, "perfusion_index": 2.0},
            {"field": "spo2", "rule": "spo2<90"},
        ),
        (
            "BP001",
            PATIENT_1,
            {"device_type": "blood_pressure", "systolic": 190, "diastolic": 100, "pulse": 80},
            {"field": "systolic", "rule": "systolic>180"},
        ),
    ]
    # Control: clinically normal heart rate must NOT alert.
    control = (
        "HR002",
        DEVICES["HR002"]["patient_id"],
        {"device_type": "heart_rate", "heart_rate": 80, "measurement_quality": "good"},
    )

    for i, (dev, pat, data, _) in enumerate(cases):
        r = await ingest_measurement(dev, pat, data, registered[dev], base + timedelta(seconds=i))
        assert r.status_code == 201
    r = await ingest_measurement(
        control[0], control[1], control[2], registered[control[0]], base + timedelta(seconds=10)
    )
    assert r.status_code == 201

    assert await wait_for_alert_count(len(cases), timeout=15.0), "alerts were not created in time"

    alerts = await db_fetch("SELECT * FROM alerts ORDER BY id")
    # Exactly the clinical cases alerted (control did not).
    assert len(alerts) == len(cases)

    for _, _, _, expected in cases:
        matches = [
            a for a in alerts if a["field"] == expected["field"] and a["rule"] == expected["rule"]
        ]
        assert len(matches) == 1, f"missing alert for {expected}"

    # Alert records carry the full structure.
    for a in alerts:
        for key in ("measurement_id", "field", "value", "rule", "severity", "timestamp"):
            assert key in a and a[key] is not None


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


async def test_idempotency(registered):
    """Identical (device_id, timestamp) submissions: 201 then 200, one DB row."""
    ts = datetime.now(UTC) - timedelta(hours=2)
    data = {"device_type": "heart_rate", "heart_rate": 72, "measurement_quality": "good"}

    r1 = await ingest_measurement("HR001", PATIENT_1, data, registered["HR001"], ts)
    assert r1.status_code == 201
    r2 = await ingest_measurement("HR001", PATIENT_1, data, registered["HR001"], ts)
    assert r2.status_code == 200
    r3 = await ingest_measurement("HR001", PATIENT_1, data, registered["HR001"], ts)
    assert r3.status_code == 200

    rows = await db_fetch(
        "SELECT COUNT(*) AS c FROM measurements WHERE device_id = $1 AND timestamp = $2",
        "HR001",
        ts,
    )
    assert rows[0]["c"] == 1


# ---------------------------------------------------------------------------
# Concurrent load (mixed normal / clinical / duplicate, all valid)
# ---------------------------------------------------------------------------


async def test_concurrent_load(registered):
    """100 concurrent submissions (normal, clinical, duplicates) all accepted."""
    base = datetime.now(UTC) - timedelta(hours=3)
    items = []
    for i in range(100):
        device_id = list(DEVICES.keys())[i % 6]
        cfg = DEVICES[device_id]
        ts = base + timedelta(seconds=i)

        if i % 10 < 5:  # normal
            if cfg["device_type"] == "heart_rate":
                data = {
                    "device_type": "heart_rate",
                    "heart_rate": 60 + (i % 40),
                    "measurement_quality": "good",
                }
            elif cfg["device_type"] == "blood_pressure":
                data = {
                    "device_type": "blood_pressure",
                    "systolic": 90 + (i % 60),
                    "diastolic": 60 + (i % 30),
                    "pulse": 60 + (i % 40),
                }
            else:
                data = {
                    "device_type": "pulse_oximeter",
                    "spo2": 95.0 + (i % 5),
                    "perfusion_index": 2.0 + (i % 5),
                }
        elif i % 10 < 6:  # clinically concerning but VALID (per spec)
            if cfg["device_type"] == "heart_rate":
                data = hr_reading(160)
            elif cfg["device_type"] == "blood_pressure":
                data = {
                    "device_type": "blood_pressure",
                    "systolic": 190,
                    "diastolic": 100,
                    "pulse": 80,
                }
            else:
                data = {"device_type": "pulse_oximeter", "spo2": 85.0, "perfusion_index": 2.0}
        else:  # duplicates (reuse an earlier timestamp)
            if cfg["device_type"] == "heart_rate":
                data = {
                    "device_type": "heart_rate",
                    "heart_rate": 72,
                    "measurement_quality": "good",
                }
            elif cfg["device_type"] == "blood_pressure":
                data = {
                    "device_type": "blood_pressure",
                    "systolic": 120,
                    "diastolic": 80,
                    "pulse": 72,
                }
            else:
                data = {"device_type": "pulse_oximeter", "spo2": 98.5, "perfusion_index": 4.5}
            ts = base  # collide on a shared timestamp -> idempotent

        items.append((device_id, cfg["patient_id"], data, registered[device_id], ts))

    responses = await asyncio.gather(
        *[ingest_measurement(d, p, data, key, ts) for d, p, data, key, ts in items]
    )
    for r in responses:
        assert r.status_code in (200, 201)

    await asyncio.sleep(1)
    duplicates = await db_fetch(
        "SELECT device_id, timestamp FROM measurements "
        "GROUP BY device_id, timestamp HAVING COUNT(*) > 1"
    )
    assert duplicates == []


# ---------------------------------------------------------------------------
# Aggregations
# ---------------------------------------------------------------------------


async def test_aggregations(registered):
    """Aggregation endpoint returns per-field avg/min/max/count over a range."""
    base = datetime.now(UTC)
    for i, hr in enumerate((60, 90, 120)):
        r = await ingest_measurement(
            "HR001",
            PATIENT_1,
            {"device_type": "heart_rate", "heart_rate": hr, "measurement_quality": "good"},
            registered["HR001"],
            base + timedelta(seconds=i),
        )
        assert r.status_code == 201

    agg = await get_aggregations(
        PATIENT_1, base - timedelta(hours=1), base + timedelta(hours=1), registered["HR001"]
    )
    assert "heart_rate" in agg
    hr_agg = agg["heart_rate"]
    assert hr_agg["count"] == 3
    assert hr_agg["min"] == 60
    assert hr_agg["max"] == 120
    assert hr_agg["min"] <= hr_agg["avg"] <= hr_agg["max"]
