from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, field_validator

from src.config import settings


class Timestamp(BaseModel):
    """Base model with timestamp validation."""

    timestamp: datetime

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, v: datetime) -> datetime:
        """Require a tz-aware timestamp that is not in the future (clock-skew tolerant).

        Measurements are observations of the past; a future timestamp is a client/clock
        error and would also corrupt the time-windowed aggregations and risk scoring.
        """
        if v.tzinfo is None:
            raise ValueError("Timestamp must be timezone-aware (use ISO 8601 with Z)")
        max_allowed = datetime.now(UTC) + timedelta(seconds=settings.MAX_FUTURE_SKEW_SECONDS)
        if v > max_allowed:
            raise ValueError("Timestamp must not be in the future")
        return v
