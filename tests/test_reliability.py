"""Reliability tests: dead-letter replay (always) + crash/chaos scenarios (opt-in).

The chaos tests kill/restart containers, so they are skipped unless RUN_CHAOS=1:

    RUN_CHAOS=1 pytest tests/test_reliability.py

They assume the live stack for the *current* branch is up (``docker compose up``).
The dead-letter replay test needs no container control and runs in the normal suite.
"""

import asyncio
import json
import os
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.helpers import (
    DEVICES,
    PATIENT_1,
    alert_count,
    assert_no_duplicate_alerts,
    assert_no_duplicate_measurements,
    assert_outbox_drained,
    count_alerts,
    db_execute,
    db_fetch,
    hr_reading,
    ingest_measurement,
    measurement_id,
    replay_failed_job,
    wait_for,
    wait_for_alert_count,
)

_ROOT = Path(__file__).resolve().parents[1]
requires_chaos = pytest.mark.skipif(
    not os.getenv("RUN_CHAOS"),
    reason="kills/restarts containers; set RUN_CHAOS=1 to run",
)

HR_CLINICAL = hr_reading(170)


async def _compose(*args: str) -> None:
    """Run a docker compose command from the repo root, off the event loop."""
    await asyncio.to_thread(
        subprocess.run,
        ["docker", "compose", *args],
        check=True,
        capture_output=True,
        cwd=_ROOT,
    )


async def _alert_count_eq(measurement_id: int, n: int) -> bool:
    return await alert_count(measurement_id) == n


async def _wait_container_healthy(container: str, timeout: float = 60.0) -> None:
    """Block until a container reports Docker health 'healthy' (after a restart)."""

    async def healthy():
        res = await asyncio.to_thread(
            subprocess.run,
            ["docker", "inspect", "-f", "{{.State.Health.Status}}", container],
            capture_output=True,
            text=True,
        )
        return res.stdout.strip() == "healthy"

    assert await wait_for(
        healthy, timeout=timeout, interval=2.0
    ), f"{container} not healthy in time"


async def _running_count(service: str) -> int:
    """Number of running container replicas for a compose service."""
    res = await asyncio.to_thread(
        subprocess.run,
        ["docker", "compose", "ps", "-q", service],
        capture_output=True,
        text=True,
        cwd=_ROOT,
    )
    return len([ln for ln in res.stdout.splitlines() if ln.strip()])


# ---------------------------------------------------------------------------
# Dead-letter replay (no container control — runs in the normal suite)
# ---------------------------------------------------------------------------


async def test_dlq_replay_runs_and_self_clears(registered):
    """A failed_jobs row replayed via the admin endpoint re-runs through the normal
    pipeline, persists its result, and clears its own dead-letter row (self-cleaning)."""
    request_id = "dlq-" + uuid.uuid4().hex
    payload = json.dumps({"patient_id": PATIENT_1, "request_id": request_id})
    await db_execute(
        "INSERT INTO failed_jobs (task_name, topic, dedup_key, payload, error, attempts) "
        "VALUES ($1, $2, $3, $4, $5, $6)",
        "compute_risk_score",
        "riskscore.requested",
        request_id,
        payload,
        "seeded failure",
        3,
    )
    job = await db_fetch("SELECT id FROM failed_jobs WHERE dedup_key = $1", request_id)
    job_id = job[0]["id"]

    resp = await replay_failed_job(job_id)
    assert resp.status_code == 202

    async def replayed_and_cleared():
        scored = await db_fetch("SELECT id FROM risk_scores WHERE request_id = $1", request_id)
        remaining = await db_fetch("SELECT id FROM failed_jobs WHERE dedup_key = $1", request_id)
        return len(scored) == 1 and len(remaining) == 0

    assert await wait_for(
        replayed_and_cleared, timeout=20.0
    ), "replay did not run / clear the DLQ row"


# ---------------------------------------------------------------------------
# Chaos (opt-in: RUN_CHAOS=1) — these kill/restart containers
# ---------------------------------------------------------------------------


@requires_chaos
async def test_worker_crash_loses_no_alert(registered):
    """Worker down while an event arrives → the durable broker queue holds the task →
    the alert is created once the worker restarts (at-least-once, nothing lost)."""
    ts = datetime.now(UTC) - timedelta(hours=8)

    await _compose("kill", "worker-alerts")
    mid = None
    try:
        r = await ingest_measurement("HR001", PATIENT_1, HR_CLINICAL, registered["HR001"], ts)
        assert r.status_code == 201
        mid = await measurement_id("HR001", ts)
        await asyncio.sleep(2)
        assert await alert_count(mid) == 0, "no alert expected while the worker is down"
    finally:
        await _compose("start", "worker-alerts")

    assert await wait_for(
        lambda: _alert_count_eq(mid, 1), timeout=40.0
    ), "alert was lost across the worker restart"
    await assert_no_duplicate_alerts()


@requires_chaos
async def test_relay_down_recovers_via_startup_drain(registered):
    """Relay down → the NOTIFY is lost and the outbox row stays unacked; when the relay
    restarts it drains the backlog → the alert is delivered. No lost job."""
    ts = datetime.now(UTC) - timedelta(hours=9)

    await _compose("stop", "outbox-relay")
    mid = None
    try:
        r = await ingest_measurement("HR001", PATIENT_1, HR_CLINICAL, registered["HR001"], ts)
        assert r.status_code == 201
        mid = await measurement_id("HR001", ts)
        await asyncio.sleep(2)
        rows = await db_fetch(
            "SELECT acked FROM outbox "
            "WHERE topic = 'measurement.created' AND (payload->>'measurement_id')::bigint = $1",
            mid,
        )
        assert rows and rows[0]["acked"] is False, "event should be committed but not yet relayed"
    finally:
        await _compose("start", "outbox-relay")

    assert await wait_for(
        lambda: _alert_count_eq(mid, 1), timeout=40.0
    ), "alert not delivered after the relay restart"
    await assert_outbox_drained(timeout=20.0)


@requires_chaos
async def test_broker_outage_keeps_events_safe(registered):
    """Broker down → the relay cannot enqueue, so the outbox row stays acked=false
    (nothing is lost — the transactional-outbox durability guarantee). Once the broker
    is back and relay + worker reconnect, the backlog drains and the alert is delivered."""
    ts = datetime.now(UTC) - timedelta(hours=10)

    await _compose("stop", "rabbitmq")
    mid = None
    try:
        # The ingest path never touches the broker, so it still succeeds.
        r = await ingest_measurement("HR001", PATIENT_1, HR_CLINICAL, registered["HR001"], ts)
        assert r.status_code == 201
        mid = await measurement_id("HR001", ts)
        await asyncio.sleep(8)  # let the relay attempt (and fail) to enqueue
        rows = await db_fetch(
            "SELECT acked FROM outbox "
            "WHERE topic = 'measurement.created' AND (payload->>'measurement_id')::bigint = $1",
            mid,
        )
        assert (
            rows and rows[0]["acked"] is False
        ), "event must stay unacked while the broker is down"
        assert await alert_count(mid) == 0
    finally:
        await _compose("start", "rabbitmq")
        await _wait_container_healthy("medical_data_rabbitmq")
        # Restart the broker clients for a deterministic reconnect.
        await _compose("restart", "outbox-relay", "worker-alerts")

    assert await wait_for(
        lambda: _alert_count_eq(mid, 1), timeout=60.0
    ), "alert not delivered after broker recovery"
    await assert_outbox_drained(timeout=30.0)


@requires_chaos
async def test_two_relays_no_double_processing(registered):
    """Run two relay replicas at once. FOR UPDATE SKIP LOCKED lets them share the
    backlog without processing the same outbox row twice: every measurement still
    yields exactly one alert, with no duplicates and a fully-acked outbox.

    (Requires the relay to be scalable — the service has no fixed container_name.)
    """
    n = 60
    await _compose("up", "-d", "--scale", "outbox-relay=2", "--no-recreate")
    try:
        assert await wait_for(
            lambda: _replicas_eq("outbox-relay", 2), timeout=30.0
        ), "two relay replicas did not come up"

        base = datetime.now(UTC) - timedelta(hours=11)
        hr_devices = [d for d, c in DEVICES.items() if c["device_type"] == "heart_rate"]
        items = []
        for i in range(n):
            dev = hr_devices[i % len(hr_devices)]
            ts = base + timedelta(milliseconds=i)
            data = hr_reading(160)
            items.append((dev, DEVICES[dev]["patient_id"], data, registered[dev], ts))

        responses = await asyncio.gather(
            *[ingest_measurement(d, p, data, k, ts) for d, p, data, k, ts in items]
        )
        assert all(r.status_code == 201 for r in responses)

        assert await wait_for_alert_count(
            n, timeout=60.0
        ), "two relays did not drain the backlog in time"

        assert await count_alerts() == n, "exactly one alert per measurement expected"
        await assert_no_duplicate_measurements()
        await assert_no_duplicate_alerts()
        await assert_outbox_drained(timeout=30.0)
    finally:
        await _compose("up", "-d", "--scale", "outbox-relay=1", "--no-recreate")


async def _replicas_eq(service: str, n: int) -> bool:
    return await _running_count(service) == n
