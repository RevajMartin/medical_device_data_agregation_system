"""Locust load test for the Medical Device Data Aggregation API.

Each simulated user represents a device: it registers itself on start (unique device_id),
then hammers POST /ingest (a configurable fraction crossing a clinical threshold so the
worker pipeline gets real work) and occasionally requests an async risk score.

Headless (CI / runbook), writes results_*.csv:
  locust -f scripts/locustfile.py --host http://localhost:8000 \
         --users 300 --spawn-rate 50 --run-time 5m --headless --csv results

Web UI (charts):
  locust -f scripts/locustfile.py --host http://localhost:8000   # http://localhost:8089

Env:
  ALERT_FRACTION  fraction of measurements that cross a clinical threshold (default 0.2)
"""

import itertools
import os
import uuid
from datetime import UTC, datetime, timedelta

from locust import HttpUser, between, task

ALERT_FRACTION = float(os.environ.get("ALERT_FRACTION", "0.2"))
PATIENT = "loadtest_patient"
# Operator token for /devices/register (must match the stack's ADMIN_API_TOKEN).
ADMIN_TOKEN = os.environ.get("ADMIN_API_TOKEN", "dev-admin-token")


class DeviceUser(HttpUser):
    # No think time: we want maximum sustained throughput for the test.
    wait_time = between(0, 0)

    def on_start(self):
        """Register this user's device and keep its API key."""
        self.device_id = f"LT-{uuid.uuid4().hex[:12]}"
        self._seq = itertools.count()
        self.api_key = None
        resp = self.client.post(
            "/devices/register",
            json={"device_id": self.device_id, "patient_id": PATIENT, "device_type": "heart_rate"},
            headers={"X-Admin-Token": ADMIN_TOKEN},
            name="POST /devices/register",
        )
        if resp.status_code == 201:
            self.api_key = resp.json()["api_key"]

    @task(20)
    def ingest(self):
        if not self.api_key:
            return
        i = next(self._seq)
        alerting = (i % 100) < int(ALERT_FRACTION * 100)
        hr = 160 if alerting else 60 + (i % 40)  # 160 > 150 -> clinical alert
        # Unique (device_id, timestamp): device_id is per-user-unique, ts increments per req.
        ts = datetime.now(UTC) + timedelta(microseconds=i)
        self.client.post(
            "/ingest/",
            name="POST /ingest/",
            headers={"X-Device-Key": self.api_key},
            json={
                "device_id": self.device_id,
                "patient_id": PATIENT,
                "timestamp": ts.isoformat(),
                "device_type": "heart_rate",
                "heart_rate": hr,
                "measurement_quality": "good",
            },
        )

    @task(1)
    def request_risk_score(self):
        # Exercises the scoring worker/queue (~5% of requests). Patient-scoped: our device
        # key belongs to PATIENT, so it authorizes the request.
        if not self.api_key:
            return
        self.client.post(
            f"/patients/{PATIENT}/risk-score",
            headers={"X-Device-Key": self.api_key},
            name="POST /patients/:id/risk-score",
        )
