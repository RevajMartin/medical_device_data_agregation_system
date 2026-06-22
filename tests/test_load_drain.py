"""Load / drain test (opt-in: RUN_LOAD=1).

Bursts N alert-worthy measurements and asserts the async pipeline **keeps up** —
every measurement yields exactly one alert within a deadline — plus the conservation
invariants (no duplicates, outbox fully drained). This is the "consumer scales with
load" guarantee; heavier sustained load (RPS, latency percentiles) lives in Locust
(`scripts/locustfile.py`).
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest

from tests.helpers import (
    DEVICES,
    assert_no_duplicate_alerts,
    assert_no_duplicate_measurements,
    assert_outbox_drained,
    db_fetch,
    ingest_measurement,
    wait_for,
)

requires_load = pytest.mark.skipif(
    not os.getenv("RUN_LOAD"), reason="slow burst/drain test; set RUN_LOAD=1 to run"
)

N = 200


@requires_load
async def test_burst_fully_drains_to_alerts(registered):
    """Fire N clinically-high measurements at once; every one must end up as exactly
    one alert, with no duplicates and a fully-drained outbox."""
    base = datetime.now(UTC) + timedelta(hours=12)
    hr_devices = [d for d, c in DEVICES.items() if c["device_type"] == "heart_rate"]

    items = []
    for i in range(N):
        dev = hr_devices[i % len(hr_devices)]
        ts = base + timedelta(milliseconds=i)  # distinct (device_id, timestamp) per request
        data = {"device_type": "heart_rate", "heart_rate": 160, "measurement_quality": "good"}
        items.append((dev, DEVICES[dev]["patient_id"], data, registered[dev], ts))

    responses = await asyncio.gather(
        *[ingest_measurement(d, p, data, k, ts) for d, p, data, k, ts in items]
    )
    bad = [r.status_code for r in responses if r.status_code != 201]
    assert not bad, f"expected all 201, got non-201: {bad[:5]}"

    rows = await db_fetch("SELECT COUNT(*) AS c FROM measurements")
    assert rows[0]["c"] == N

    async def all_alerts():
        a = await db_fetch("SELECT COUNT(*) AS c FROM alerts")
        return a[0]["c"] >= N

    assert await wait_for(all_alerts, timeout=90.0), "pipeline did not drain all alerts in time"

    alerts = await db_fetch("SELECT COUNT(*) AS c FROM alerts")
    assert alerts[0]["c"] == N, "exactly one alert per measurement expected"

    await assert_no_duplicate_measurements()
    await assert_no_duplicate_alerts()
    await assert_outbox_drained(timeout=30.0)
