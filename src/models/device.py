from sqlalchemy import Boolean, Column, ForeignKey, Index, String
from sqlalchemy.orm import relationship

from .base import Base


class Patient(Base):
    """Represents a patient who owns medical devices."""

    __tablename__ = "patients"

    id = Column(String(50), primary_key=True)
    name = Column(String(255))

    # Relationships
    devices = relationship("Device", back_populates="patient", cascade="all, delete-orphan")

    __table_args__ = (Index("idx_patient_id", "id"),)


class Device(Base):
    """Represents a medical device that submits measurements."""

    __tablename__ = "devices"

    id = Column(String(50), primary_key=True)  # e.g., "HR001", "BP002"
    patient_id = Column(String(50), ForeignKey("patients.id"), nullable=False)
    type = Column(String(50), nullable=False)  # heart_rate | blood_pressure | pulse_oximeter
    api_key_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    # Relationships
    patient = relationship("Patient", back_populates="devices")
    measurements = relationship("Measurement", back_populates="device")

    __table_args__ = (
        Index("idx_device_patient", "patient_id"),
        Index("idx_device_type", "type"),
        Index("idx_device_api_key", "api_key_hash", unique=True),
    )
