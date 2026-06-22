from datetime import datetime

from pydantic import BaseModel, field_validator


class Timestamp(BaseModel):
    """Base model with timestamp validation."""

    timestamp: datetime

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, v: datetime) -> datetime:
        """Ensure timestamp is timezone-aware."""
        if v.tzinfo is None:
            raise ValueError("Timestamp must be timezone-aware (use ISO 8601 with Z)")
        return v
