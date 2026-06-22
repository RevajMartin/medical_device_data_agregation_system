from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .base import Base


class Alert(Base):
    """Represents a clinical alert triggered by threshold violation."""

    __tablename__ = "alerts"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    measurement_id = Column(BigInteger, ForeignKey("measurements.id"), nullable=False)
    patient_id = Column(String(50), ForeignKey("patients.id"), nullable=False)
    device_type = Column(String(50), nullable=False)
    field = Column(String(50), nullable=False)  # e.g., "heart_rate", "systolic"
    value = Column(Float, nullable=False)  # The value that crossed threshold
    timestamp = Column(DateTime(timezone=True), nullable=False)
    rule = Column(String(100), nullable=False)  # e.g., "heart_rate>150", "spo2<90"
    severity = Column(String(20), nullable=True)  # e.g., "high", "critical"
    resolved = Column(Boolean, default=False, nullable=False)
    resolved_at = Column(DateTime(timezone=True))
    resolved_by = Column(String(255))

    # Relationships
    measurement = relationship("Measurement", back_populates="alerts")

    __table_args__ = (
        # Idempotency: same measurement + rule = same alert
        UniqueConstraint("measurement_id", "rule", name="uq_alert_measurement_rule"),
        Index("idx_alert_patient", "patient_id"),
        Index("idx_alert_timestamp", "timestamp"),
        Index("idx_alert_resolved", "resolved", "resolved_at"),
    )
