"""Risk-scoring handler (rule-based heuristic, not ML).

Plain ``async def`` called by the generic consumer runner on the ``scoring`` queue, so it
runs in a separate worker container, isolated from latency-sensitive alerts.

The feature query is **bounded** (recent time window AND a row cap), so a single job's cost
is independent of how many measurements a patient has. ``RiskScorer.score`` is a small,
swappable boundary — a real model could replace it without changing any wiring.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from src.config import settings
from src.database import async_session_maker
from src.models.measurement import Measurement
from src.repositories import measurements as measurements_repo
from src.repositories import risk_scores as risk_scores_repo
from src.repositories.failed_jobs import (
    clear_success as clear_failure,
)
from src.repositories.failed_jobs import (
    upsert_failure as record_failure,
)

logger = logging.getLogger(__name__)


class RiskScorer:
    """Rule-based risk scorer (heuristic, not ML). Swap `score()` for a real model later."""

    version = "rules-v0"

    def score(self, features: dict[str, Any]) -> tuple[float, str]:
        """Map physiological features to a 0..100 risk score and a level."""
        risk = 0.0
        hr = features.get("hr_mean")
        spo2 = features.get("spo2_min")
        systolic = features.get("systolic_max")
        if hr is not None:
            risk += max(0.0, hr - 100) * 0.6  # tachycardia
        if spo2 is not None:
            risk += max(0.0, 95 - spo2) * 3.0  # hypoxemia
        if systolic is not None:
            risk += max(0.0, systolic - 120) * 0.4  # hypertension
        risk = round(min(100.0, risk), 1)
        level = "high" if risk >= 70 else "medium" if risk >= 30 else "low"
        return risk, level


def _extract_features(measurements: list[Measurement]) -> dict[str, Any]:
    """Aggregate recent measurements into scorer features."""
    hrs, spo2s, systolics = [], [], []
    for m in measurements:
        if m.device_type == "heart_rate" and m.data.get("heart_rate") is not None:
            hrs.append(m.data["heart_rate"])
        elif m.device_type == "blood_pressure" and m.data.get("systolic") is not None:
            systolics.append(m.data["systolic"])
        elif m.device_type == "pulse_oximeter" and m.data.get("spo2") is not None:
            spo2s.append(m.data["spo2"])

    features: dict[str, Any] = {"sample_count": len(measurements)}
    if hrs:
        features["hr_mean"] = round(sum(hrs) / len(hrs), 1)
    if spo2s:
        features["spo2_min"] = min(spo2s)
    if systolics:
        features["systolic_max"] = max(systolics)
    return features


async def compute_risk_score(message: dict[str, Any]) -> None:
    """Compute a patient risk score from recent vitals and persist it (idempotent)."""
    patient_id = message["patient_id"]
    request_id = message["request_id"]
    logger.info(f"Computing risk score for {patient_id} ({request_id})")

    try:
        async with async_session_maker() as db:
            since = datetime.now(UTC) - timedelta(hours=settings.RISK_WINDOW_HOURS)
            rows = await measurements_repo.get_windowed(
                db, patient_id, since, settings.RISK_MAX_SAMPLES
            )

            features = _extract_features(rows)
            scorer = RiskScorer()
            risk, level = scorer.score(features)

            created = await risk_scores_repo.insert_idempotent(
                db,
                request_id=request_id,
                patient_id=patient_id,
                score=risk,
                level=level,
                scorer_version=scorer.version,
                details=features,
            )
            await clear_failure(db, "compute_risk_score", request_id)
            await db.commit()
            if created:
                logger.info(
                    f"Risk score for {patient_id}: {risk} ({level}) "
                    f"[{scorer.version}] features={features}"
                )
            else:
                logger.debug(f"Risk score {request_id} already exists (idempotent)")
    except Exception as exc:
        await record_failure(
            "compute_risk_score", "riskscore.requested", request_id, message, repr(exc)
        )
        raise
