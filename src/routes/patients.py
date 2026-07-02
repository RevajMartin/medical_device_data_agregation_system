"""Patient-level endpoints, incl. requesting an async risk score."""

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.outbox.producer import emit_outbox_event
from src.repositories import risk_scores as risk_scores_repo
from src.schemas.responses import RiskScoreListResponse, RiskScoreRequestResponse
from src.services.device_auth import require_patient_scope

# Patient-scoped: every route is /{patient_id}/..., guarded by the device key's patient scope.
router = APIRouter(
    prefix="/patients",
    tags=["patients"],
    dependencies=[Depends(require_patient_scope)],
    responses={
        401: {"description": "Missing or invalid X-Device-Key"},
        403: {"description": "API key not authorized for this patient"},
    },
)


@router.post(
    "/{patient_id}/risk-score",
    response_model=RiskScoreRequestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_risk_score(patient_id: str, db: AsyncSession = Depends(get_db)):
    """
    Request an asynchronous (rule-based) risk score for a patient.

    Emits a `riskscore.requested` event (transactional outbox); the relay dispatches
    it to the `compute_risk_score` worker task. Returns 202 with a request_id the
    client can use to look up the result via GET.
    """
    request_id = uuid.uuid4().hex
    await emit_outbox_event(
        db, "riskscore.requested", {"patient_id": patient_id, "request_id": request_id}
    )
    return RiskScoreRequestResponse(
        status="processing", request_id=request_id, patient_id=patient_id
    )


@router.get("/{patient_id}/risk-scores", response_model=RiskScoreListResponse)
async def get_risk_scores(
    patient_id: str,
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
):
    """Return the most recent computed risk scores for a patient."""
    scores = await risk_scores_repo.list_for_patient(db, patient_id, limit)
    return RiskScoreListResponse(
        patient_id=patient_id,
        risk_scores=[
            {
                "request_id": s.request_id,
                "score": s.score,
                "level": s.level,
                "scorer_version": s.scorer_version,
                "details": s.details,
                "created_at": s.created_at,
            }
            for s in scores
        ],
    )
