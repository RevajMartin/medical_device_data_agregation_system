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
    count_alerts,
    hr_reading,
    ingest_measurement,
    wait_for_alert_count,
    wait_for_measurement_count,
)

requires_load = pytest.mark.skipif(
    not os.getenv("RUN_LOAD"), reason="slow burst/drain test; set RUN_LOAD=1 to run"
)

N = 200


@requires_load
async def test_burst_fully_drains_to_alerts(registered):
    """Fire N clinically-high measurements at once; every one must end up as exactly
    one alert, with no duplicates and a fully-drained outbox."""
    base = datetime.now(UTC) - timedelta(hours=12)
    hr_devices = [d for d, c in DEVICES.items() if c["device_type"] == "heart_rate"]

    items = []
    for i in range(N):
        dev = hr_devices[i % len(hr_devices)]
        ts = base + timedelta(milliseconds=i)  # distinct (device_id, timestamp) per request
        data = hr_reading(160)
        items.append((dev, DEVICES[dev]["patient_id"], data, registered[dev], ts))

    responses = await asyncio.gather(
        *[ingest_measurement(d, p, data, k, ts) for d, p, data, k, ts in items]
    )
    bad = [r.status_code for r in responses if r.status_code != 201]
    assert not bad, f"expected all 201, got non-201: {bad[:5]}"

    # The API commits in its request-teardown (after the 201 is returned), so some commits
    # can still be in flight right after gather() returns; poll for the count.
    assert await wait_for_measurement_count(N, timeout=15.0), "not all measurements were committed"

    assert await wait_for_alert_count(N, timeout=90.0), "pipeline did not drain all alerts in time"

    assert await count_alerts() == N, "exactly one alert per measurement expected"

    await assert_no_duplicate_measurements()
    await assert_no_duplicate_alerts()
    await assert_outbox_drained(timeout=30.0)
