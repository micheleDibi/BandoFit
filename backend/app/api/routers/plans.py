from fastapi import APIRouter

from app.api.deps import PrimaryClient
from app.schemas.plan import PlanOut
from app.services import plan_service

router = APIRouter(prefix="/plans", tags=["plans"])


@router.get("", response_model=list[PlanOut])
async def list_plans(primary: PrimaryClient) -> list[PlanOut]:
    """Piani attivi, visibili anche senza login (servono alla registrazione)."""
    return await plan_service.list_active_plans(primary)
