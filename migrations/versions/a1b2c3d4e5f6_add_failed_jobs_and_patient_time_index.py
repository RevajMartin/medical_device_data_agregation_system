"""add failed_jobs (dead-letter) and measurements(patient_id, timestamp) index

Revision ID: a1b2c3d4e5f6
Revises: 720316494099
Create Date: 2026-06-17

- failed_jobs: dead-letter record for poison messages (see src/tasks/dead_letter.py).
- idx_measurement_patient_time: supports the windowed risk-scoring query
  (latest N measurements per patient, ORDER BY timestamp DESC LIMIT N).
"""

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "720316494099"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "failed_jobs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("task_name", sa.String(length=100), nullable=False),
        sa.Column("topic", sa.String(length=100), nullable=False),
        sa.Column("dedup_key", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_name", "dedup_key", name="uq_failed_job_task_dedup"),
    )
    op.create_index("idx_failed_job_updated", "failed_jobs", ["updated_at"])
    op.create_index("idx_measurement_patient_time", "measurements", ["patient_id", "timestamp"])


def downgrade() -> None:
    op.drop_index("idx_measurement_patient_time", table_name="measurements")
    op.drop_index("idx_failed_job_updated", table_name="failed_jobs")
    op.drop_table("failed_jobs")
