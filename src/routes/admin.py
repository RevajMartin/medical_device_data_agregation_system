"""Admin endpoints for the dead-letter queue (failed background jobs).

Lets an operator see jobs that exhausted their retries and replay them once the
underlying issue is fixed. Replay re-emits the original outbox event, so it flows
through the normal relay -> worker pipeline and is idempotent (per-job UNIQUE
constraints); a successful replay makes the task clear its own `failed_jobs` row.
"""


from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.outbox.producer import emit_outbox_event
from src.repositories import failed_jobs as failed_jobs_repo
from src.schemas.responses import FailedJobResponse, ReplayResponse
from src.services.device_auth import require_admin

# Operator-only: every route requires a valid X-Admin-Token (checked before any lookup).
router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/failed-jobs", response_model=list[FailedJobResponse])
async def list_failed_jobs(db: AsyncSession = Depends(get_db)):
    """List dead-lettered jobs (most recently failed first)."""
    jobs = await failed_jobs_repo.list_all(db)
    return [FailedJobResponse.model_validate(job) for job in jobs]


@router.post(
    "/failed-jobs/{job_id}/replay",
    response_model=ReplayResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def replay_failed_job(job_id: int, db: AsyncSession = Depends(get_db)):
    """Re-emit a failed job's original event; the relay re-dispatches it to the worker."""
    job = await failed_jobs_repo.get_by_id(db, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="failed job not found")
    await emit_outbox_event(db, job.topic, job.payload)
    return ReplayResponse(status="replay queued", id=job_id, topic=job.topic)
