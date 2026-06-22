from sqlalchemy import JSON, BigInteger, Column, Float, ForeignKey, Index, String, UniqueConstraint

from .base import Base


class RiskScore(Base):
    """A computed patient risk score (output of the rule-based risk-scoring background job)."""

    __tablename__ = "risk_scores"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    # Idempotency key from the request: redelivery of the same job -> one row.
    request_id = Column(String(64), nullable=False)
    patient_id = Column(String(50), ForeignKey("patients.id"), nullable=False)
    score = Column(Float, nullable=False)  # 0..100
    level = Column(String(20), nullable=False)  # low | medium | high
    scorer_version = Column(String(50), nullable=False)  # e.g. "rules-v0"
    details = Column(JSON, nullable=True)  # features the score was computed from

    __table_args__ = (
        UniqueConstraint("request_id", name="uq_risk_score_request"),
        Index("idx_risk_score_patient", "patient_id", "created_at"),
    )
