from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class APIResponse(BaseModel):
    """Base for all API response models. Enables direct validation from ORM objects."""

    model_config = ConfigDict(from_attributes=True)


class IngestResponse(APIResponse):
    status: str
    message: str


class RiskScoreRequestResponse(APIResponse):
    status: str
    request_id: str
    patient_id: str


class RiskScoreItem(APIResponse):
    request_id: str
    score: float
    level: str
    scorer_version: str
    details: dict[str, Any]
    created_at: datetime | None = None


class RiskScoreListResponse(APIResponse):
    patient_id: str
    risk_scores: list[RiskScoreItem]


class FieldStats(APIResponse):
    avg: float | None = None
    min: float | None = None
    max: float | None = None
    count: int


class AggregationResponse(APIResponse):
    patient_id: str
    start: datetime
    end: datetime
    total_count: int
    heart_rate: FieldStats | None = None
    systolic: FieldStats | None = None
    diastolic: FieldStats | None = None
    pulse: FieldStats | None = None
    spo2: FieldStats | None = None
    perfusion_index: FieldStats | None = None


class FailedJobResponse(APIResponse):
    id: int
    task_name: str
    topic: str
    dedup_key: str
    attempts: int
    error: str | None = None
    payload: dict[str, Any]
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ReplayResponse(APIResponse):
    status: str
    id: int
    topic: str


class HealthResponse(APIResponse):
    status: str
    app: str
