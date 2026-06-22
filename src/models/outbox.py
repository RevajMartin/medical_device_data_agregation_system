from sqlalchemy import JSON, BigInteger, Boolean, Column, DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import relationship

from .base import Base


class Outbox(Base):
    """Transactional outbox table for reliable event publishing."""

    __tablename__ = "outbox"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    topic = Column(String(255), nullable=False)  # e.g., "measurement.created"
    payload = Column(JSON, nullable=False)  # {"measurement_id": 123, ...}
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    acked = Column(Boolean, default=False, nullable=False)  # Processed by relay?

    # Relationship (optional, if needed)
    measurement_id = Column(BigInteger, ForeignKey("measurements.id"))
    measurement = relationship("Measurement", back_populates="outbox")

    __table_args__ = (
        # Index for efficient polling of unacked messages
        Index("idx_outbox_acked", "acked"),
        Index("idx_outbox_created_at", "created_at"),
    )
