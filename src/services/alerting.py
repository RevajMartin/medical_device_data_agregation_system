"""Alerting service for clinical threshold evaluation.

Single source of truth for which physiological values trigger an alert. Values
reaching this layer are already known valid (Pydantic enforces 422 for out-of-range);
here we flag clinically concerning values per the spec:
heart_rate > 150, spo2 < 90, systolic > 180.
"""

from datetime import datetime
from typing import Any

from src.config import settings


def check_thresholds(
    device_type: str, data: dict[str, Any], timestamp: datetime
) -> list[dict[str, Any]]:
    """
    Evaluate clinical thresholds for a single measurement.

    Returns a list of alert dicts (field, value, rule, severity, timestamp) — empty if
    no threshold is breached. Persistence is handled by the caller via alerts_repo.
    """
    alerts: list[dict[str, Any]] = []

    if device_type == "heart_rate":
        hr = data.get("heart_rate")
        if hr is not None and hr > settings.HR_ALERT_MAX:
            alerts.append(
                {
                    "field": "heart_rate",
                    "value": hr,
                    "rule": f"heart_rate>{settings.HR_ALERT_MAX}",
                    "severity": "high",
                    "timestamp": timestamp,
                }
            )

    elif device_type == "blood_pressure":
        systolic = data.get("systolic")
        if systolic is not None and systolic > settings.SYSTOLIC_ALERT_MAX:
            alerts.append(
                {
                    "field": "systolic",
                    "value": systolic,
                    "rule": f"systolic>{settings.SYSTOLIC_ALERT_MAX}",
                    "severity": "critical",
                    "timestamp": timestamp,
                }
            )

    elif device_type == "pulse_oximeter":
        spo2 = data.get("spo2")
        if spo2 is not None and spo2 < settings.SPO2_ALERT_MIN:
            alerts.append(
                {
                    "field": "spo2",
                    "value": spo2,
                    "rule": f"spo2<{settings.SPO2_ALERT_MIN:g}",
                    "severity": "critical",
                    "timestamp": timestamp,
                }
            )

    return alerts
