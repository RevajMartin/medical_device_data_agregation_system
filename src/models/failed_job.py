from sqlalchemy import JSON, BigInteger, Column, Index, Integer, String, Text, UniqueConstraint

from .base import Base


class FailedJob(Base):
    """A background job that exhausted its retries (dead-letter record).

    Recorded so no medical event is ever silently dropped after a poison-message
    failure; the task clears its row on a later success (self-cleaning), so only
    genuinely-failed jobs remain. Replayable via the admin endpoint, which re-emits
    the original outbox event through the normal pipeline.
    """

    __tablename__ = "failed_jobs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    task_name = Column(String(100), nullable=False)  # e.g. "compute_risk_score"
    topic = Column(
        String(100), nullable=False
    )  # outbox topic to replay, e.g. "riskscore.requested"
    dedup_key = Column(
        String(128), nullable=False
    )  # request_id / measurement_id -> one row per job
    payload = Column(JSON, nullable=False)  # the task message (== outbox payload)
    error = Column(Text, nullable=True)  # last exception (repr)
    attempts = Column(Integer, nullable=False, default=1)

    __table_args__ = (
        UniqueConstraint("task_name", "dedup_key", name="uq_failed_job_task_dedup"),
        Index("idx_failed_job_updated", "updated_at"),
    )
