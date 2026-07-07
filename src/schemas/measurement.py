from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.config import settings

from .common import Timestamp

# ========== COMMON ENVELOPE ==========

# Self-reported quality flag (fixed vocabulary rather than free text).
MeasurementQuality = Literal["good", "fair", "poor"]


class _MeasurementBase(Timestamp):
    """Fields shared by every measurement payload (the discriminated-union envelope)."""

    # Reject unknown fields so a typo'd / spoofed key can't slip through silently.
    model_config = ConfigDict(extra="forbid")

    # Same id charset as DeviceRegister, so a malformed id is a 422 here too (not a later 403).
    device_id: str = Field(..., min_length=1, max_length=50, pattern=r"^[a-zA-Z0-9_-]+$")
    patient_id: str = Field(..., min_length=1, max_length=50)


# ========== DEVICE-SPECIFIC SUBMISSIONS ==========


class HeartRateMeasurement(_MeasurementBase):
    """Heart Rate Monitor submission."""

    device_type: Literal["heart_rate"] = "heart_rate"
    heart_rate: int = Field(
        ..., ge=settings.HR_MIN, le=settings.HR_MAX, description="Heart rate in bpm"
    )
    measurement_quality: MeasurementQuality = "good"


class BloodPressureMeasurement(_MeasurementBase):
    """Blood Pressure Monitor submission."""

    device_type: Literal["blood_pressure"] = "blood_pressure"
    systolic: int = Field(
        ...,
        ge=settings.SYSTOLIC_MIN,
        le=settings.SYSTOLIC_MAX,
        description="Systolic pressure in mmHg",
    )
    diastolic: int = Field(
        ...,
        ge=settings.DIASTOLIC_MIN,
        le=settings.DIASTOLIC_MAX,
        description="Diastolic pressure in mmHg",
    )
    pulse: int = Field(..., ge=settings.HR_MIN, le=settings.HR_MAX, description="Pulse rate in bpm")

    @model_validator(mode="after")
    def check_systolic_diastolic(self) -> "BloodPressureMeasurement":
        """Ensure systolic > diastolic."""
        if self.systolic <= self.diastolic:
            raise ValueError("systolic must be greater than diastolic")
        return self


class PulseOximeterMeasurement(_MeasurementBase):
    """Pulse Oximeter submission."""

    device_type: Literal["pulse_oximeter"] = "pulse_oximeter"
    spo2: float = Field(
        ..., ge=settings.SPO2_MIN, le=settings.SPO2_MAX, description="Oxygen saturation in %"
    )
    perfusion_index: float = Field(..., gt=0, description="Perfusion index (must be positive)")


# ========== INGESTION SCHEMA ==========

# Single ingestion endpoint: a flat, top-level Pydantic discriminated union on
# `device_type` (matches the payload examples in the task spec). Pydantic routes
# each request to the matching model and applies its physiological validators.
MeasurementRequest = Annotated[
    HeartRateMeasurement | BloodPressureMeasurement | PulseOximeterMeasurement,
    Field(discriminator="device_type"),
]

# Envelope fields live in their own columns; everything else is the JSON `data` column.
_ENVELOPE_FIELDS = {"device_id", "patient_id", "timestamp", "device_type"}


def measurement_data(payload: BaseModel) -> dict:
    """Device-specific fields of a validated measurement (the JSON ``data`` column)."""
    return payload.model_dump(exclude=_ENVELOPE_FIELDS)
