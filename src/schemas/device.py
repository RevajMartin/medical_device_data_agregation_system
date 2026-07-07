from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DeviceRegister(BaseModel):
    """Schema for device registration."""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(..., min_length=1, max_length=50, pattern=r"^[a-zA-Z0-9_-]+$")
    patient_id: str = Field(..., min_length=1, max_length=50)
    device_type: Literal["heart_rate", "blood_pressure", "pulse_oximeter"]


class DeviceRegisterResponse(BaseModel):
    """Response after successful device registration."""

    device_id: str
    patient_id: str
    api_key: str = Field(..., description="Generated API key for device authentication")
    message: str = "Device registered successfully"
