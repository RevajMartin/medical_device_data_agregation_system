"""
Contract tests for API error semantics defined by the specification:

  * Physiological validation -> 422 (out-of-range, systolic <= diastolic,
    non-positive perfusion_index); valid boundary values -> 201.
  * Device authentication -> 401 (missing/invalid key), 403 (wrong patient scope).

These complement the happy-path integration scenario in test_load_scenario.py.
"""

from datetime import UTC, datetime

import pytest

from tests.helpers import DEVICES, PATIENT_1, PATIENT_2, ingest_measurement

# (device_id, data, expected_status)
VALIDATION_CASES = [
    # heart_rate: integer in [30, 250]
    ("HR001", {"device_type": "heart_rate", "heart_rate": 260}, 422),
    ("HR001", {"device_type": "heart_rate", "heart_rate": 25}, 422),
    ("HR001", {"device_type": "heart_rate", "heart_rate": 30, "measurement_quality": "good"}, 201),
    ("HR001", {"device_type": "heart_rate", "heart_rate": 250, "measurement_quality": "good"}, 201),
    # blood_pressure: systolic [60,250], diastolic [40,150], pulse [30,250], systolic > diastolic
    (
        "BP001",
        {"device_type": "blood_pressure", "systolic": 260, "diastolic": 100, "pulse": 80},
        422,
    ),
    ("BP001", {"device_type": "blood_pressure", "systolic": 50, "diastolic": 45, "pulse": 60}, 422),
    (
        "BP001",
        {"device_type": "blood_pressure", "systolic": 200, "diastolic": 160, "pulse": 80},
        422,
    ),
    (
        "BP001",
        {"device_type": "blood_pressure", "systolic": 120, "diastolic": 30, "pulse": 80},
        422,
    ),
    ("BP001", {"device_type": "blood_pressure", "systolic": 80, "diastolic": 90, "pulse": 70}, 422),
    (
        "BP001",
        {"device_type": "blood_pressure", "systolic": 120, "diastolic": 80, "pulse": 260},
        422,
    ),
    (
        "BP001",
        {"device_type": "blood_pressure", "systolic": 120, "diastolic": 80, "pulse": 72},
        201,
    ),
    # pulse_oximeter: spo2 [50,100], perfusion_index > 0
    ("PO001", {"device_type": "pulse_oximeter", "spo2": 45.0, "perfusion_index": 1.0}, 422),
    ("PO001", {"device_type": "pulse_oximeter", "spo2": 105.0, "perfusion_index": 1.0}, 422),
    ("PO001", {"device_type": "pulse_oximeter", "spo2": 98.0, "perfusion_index": 0.0}, 422),
    ("PO001", {"device_type": "pulse_oximeter", "spo2": 98.0, "perfusion_index": -1.0}, 422),
    ("PO001", {"device_type": "pulse_oximeter", "spo2": 98.0, "perfusion_index": 1.4}, 201),
]


@pytest.mark.parametrize(
    "device_id, data, expected",
    VALIDATION_CASES,
    ids=[f"{c[0]}-{c[2]}-{i}" for i, c in enumerate(VALIDATION_CASES)],
)
async def test_physiological_validation(registered, device_id, data, expected):
    patient_id = DEVICES[device_id]["patient_id"]
    resp = await ingest_measurement(device_id, patient_id, data, registered[device_id])
    assert resp.status_code == expected


async def test_missing_api_key_returns_401(registered):
    data = {"device_type": "heart_rate", "heart_rate": 72, "measurement_quality": "good"}
    resp = await ingest_measurement("HR001", PATIENT_1, data, api_key=None)
    assert resp.status_code == 401


async def test_invalid_api_key_returns_401(registered):
    data = {"device_type": "heart_rate", "heart_rate": 72, "measurement_quality": "good"}
    resp = await ingest_measurement("HR001", PATIENT_1, data, api_key="deadbeef" * 8)
    assert resp.status_code == 401


async def test_wrong_patient_scope_returns_403(registered):
    # HR001 belongs to PATIENT_1; submitting for PATIENT_2 with its key must be forbidden.
    data = {"device_type": "heart_rate", "heart_rate": 72, "measurement_quality": "good"}
    resp = await ingest_measurement("HR001", PATIENT_2, data, registered["HR001"])
    assert resp.status_code == 403


async def test_valid_submission_succeeds(registered):
    data = {"device_type": "heart_rate", "heart_rate": 72, "measurement_quality": "good"}
    resp = await ingest_measurement(
        "HR001", PATIENT_1, data, registered["HR001"], timestamp=datetime.now(UTC)
    )
    assert resp.status_code == 201
