"""Authentication / authorization + input-validation contract tests.

Encodes the security behaviour the reviewers flagged as missing (and which the suite must
enforce going forward): the read endpoints and risk-score request are patient-scoped via
``X-Device-Key``; ``/admin/*`` and ``/devices/register`` require the operator ``X-Admin-Token``;
out-of-policy input (future timestamps, unknown fields, malformed device ids) is rejected 422.

Runs against the live stack. Written test-first: these fail against the unfixed app and pass
once the auth dependencies and schema validation land.
"""

from datetime import UTC, datetime, timedelta

import httpx

from tests.helpers import (
    ADMIN_TOKEN,
    BASE_URL,
    PATIENT_1,
    get_risk_scores,
    hr_reading,
    ingest_measurement,
    replay_failed_job,
    request_risk_score,
)

HR = hr_reading()


# ---------------------------------------------------------------------------
# Patient-scoped reads (X-Device-Key + the key's patient must match the path)
# ---------------------------------------------------------------------------


async def test_aggregations_requires_key(registered):
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{BASE_URL}/aggregations/{PATIENT_1}")
    assert resp.status_code == 401


async def test_aggregations_wrong_patient_forbidden(registered):
    # HR002 is PATIENT_2's device; it must not read PATIENT_1's aggregations.
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BASE_URL}/aggregations/{PATIENT_1}",
            headers={"X-Device-Key": registered["HR002"]},
        )
    assert resp.status_code == 403


async def test_aggregations_authorized(registered):
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BASE_URL}/aggregations/{PATIENT_1}",
            headers={"X-Device-Key": registered["HR001"]},
        )
    assert resp.status_code == 200


async def test_risk_score_request_requires_key(registered):
    resp = await request_risk_score(PATIENT_1, api_key=None)
    assert resp.status_code == 401


async def test_risk_score_request_wrong_patient_forbidden(registered):
    resp = await request_risk_score(PATIENT_1, api_key=registered["HR002"])
    assert resp.status_code == 403


async def test_risk_scores_list_requires_key(registered):
    resp = await get_risk_scores(PATIENT_1, api_key=None)
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Admin token (operator-scoped: /admin/*, /devices/register)
# ---------------------------------------------------------------------------


async def test_admin_failed_jobs_requires_token(registered):
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{BASE_URL}/admin/failed-jobs")
    assert resp.status_code == 401


async def test_admin_failed_jobs_authorized(registered):
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BASE_URL}/admin/failed-jobs", headers={"X-Admin-Token": ADMIN_TOKEN}
        )
    assert resp.status_code == 200


async def test_admin_replay_requires_token(registered):
    # Auth is checked before the job lookup, so a missing token is 401 regardless of job id.
    resp = await replay_failed_job(999999, admin_token=None)
    assert resp.status_code == 401


async def test_register_requires_admin_token(clean_db):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{BASE_URL}/devices/register",
            json={"device_id": "AUTHTEST1", "patient_id": PATIENT_1, "device_type": "heart_rate"},
        )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Input validation (422)
# ---------------------------------------------------------------------------


async def test_future_timestamp_rejected(registered):
    future = datetime.now(UTC) + timedelta(hours=1)
    resp = await ingest_measurement("HR001", PATIENT_1, HR, registered["HR001"], future)
    assert resp.status_code == 422


async def test_unknown_field_rejected(registered):
    data = {**HR, "totally_unknown_field": 123}
    resp = await ingest_measurement("HR001", PATIENT_1, data, registered["HR001"])
    assert resp.status_code == 422


async def test_malformed_device_id_rejected(registered):
    # device_id violates the registration regex (^[a-zA-Z0-9_-]+$); must 422 on ingest too.
    resp = await ingest_measurement("bad id!", PATIENT_1, HR, registered["HR001"])
    assert resp.status_code == 422
