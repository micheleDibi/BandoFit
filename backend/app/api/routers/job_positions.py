from fastapi import APIRouter

from app.api.deps import PrimaryClient
from app.schemas.job_position import JobPositionOut
from app.services import job_position_service

router = APIRouter(prefix="/job-positions", tags=["job-positions"])


@router.get("", response_model=list[JobPositionOut])
async def list_job_positions(primary: PrimaryClient) -> list[JobPositionOut]:
    """Posizioni attive, visibili anche senza login (servono alla registrazione)."""
    return await job_position_service.list_active_positions(primary)
