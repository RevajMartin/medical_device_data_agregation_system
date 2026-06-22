"""Alert handler: evaluate clinical thresholds for a measurement and persist alerts.

Plain ``async def`` (no task framework) called by the generic consumer runner. Runs the
application's async SQLAlchemy code directly on the consumer's event loop. Raising
propagates to the runner (no ack -> redelivery / bounded retry); a job that keeps failing
is captured in ``failed_jobs`` (dead-letter) here and cleared on success, so nothing is
silently dropped.
"""

import logging
from typing import Any

from src.database import async_session_maker
from src.repositories import alerts as alerts_repo
from src.repositories import measurements as measurements_repo
from src.repositories.failed_jobs import (
    clear_success as clear_failure,
)
from src.repositories.failed_jobs import (
    upsert_failure as record_failure,
)
from src.services.alerting import check_thresholds

logger = logging.getLogger(__name__)


async def process_measurement(message: dict[str, Any]) -> None:
    """Evaluate clinical thresholds for a measurement and persist alerts idempotently."""
    measurement_id = message["measurement_id"]
    logger.info(f"Processing measurement {measurement_id}")

    try:
        async with async_session_maker() as db:
            measurement = await measurements_repo.get_by_id(db, measurement_id)

            if measurement is None:
                logger.warning(f"Measurement {measurement_id} not found")
                await clear_failure(db, "process_measurement", str(measurement_id))
                await db.commit()
                return

            triggered = check_thresholds(
                measurement.device_type, measurement.data, measurement.timestamp
            )

            created = 0
            for alert in triggered:
                if await alerts_repo.insert_idempotent(
                    db,
                    measurement_id=measurement_id,
                    patient_id=measurement.patient_id,
                    device_type=measurement.device_type,
                    field=alert["field"],
                    value=alert["value"],
                    rule=alert["rule"],
                    severity=alert["severity"],
                    timestamp=alert["timestamp"],
                ):
                    created += 1

            await clear_failure(db, "process_measurement", str(measurement_id))
            await db.commit()
            if triggered:
                logger.info(
                    f"Measurement {measurement_id}: {created} new alert(s) created, "
                    f"{len(triggered) - created} already existed"
                )
    except Exception as exc:
        await record_failure(
            "process_measurement", "measurement.created", str(measurement_id), message, repr(exc)
        )
        raise
