from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .base import Base


class Measurement(Base):
    """Represents a physiological measurement from a medical device."""

    __tablename__ = "measurements"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    device_id = Column(String(50), ForeignKey("devices.id"), nullable=False)
    patient_id = Column(String(50), ForeignKey("patients.id"), nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False)
    device_type = Column(String(50), nullable=False)  # Discriminator
    data = Column(JSON, nullable=False)  # Device-specific fields

    # Relationships
    device = relationship("Device", back_populates="measurements")
    alerts = relationship("Alert", back_populates="measurement")
    outbox = relationship("Outbox", back_populates="measurement", uselist=False)

    __table_args__ = (
        # Idempotency: same (device_id, timestamp) = same measurement
        UniqueConstraint("device_id", "timestamp", name="uq_measurement_device_timestamp"),
        Index("idx_measurement_patient_device_time", "patient_id", "device_type", "timestamp"),
        Index("idx_measurement_timestamp", "timestamp"),
        Index("idx_measurement_patient", "patient_id"),
    )
