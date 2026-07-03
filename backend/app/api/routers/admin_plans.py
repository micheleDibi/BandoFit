from fastapi import APIRouter

from app.api.deps import AdminUser, PrimaryClient
from app.schemas.plan import PlanCreate, PlanOut, PlanUpdate
from app.services import plan_service

router = APIRouter(prefix="/admin/plans", tags=["admin"])


@router.get("", response_model=list[PlanOut])
async def list_plans(_admin: AdminUser, primary: PrimaryClient) -> list[PlanOut]:
    """Tutti i piani, inclusi quelli disattivati."""
    return await plan_service.list_all_plans(primary)


@router.post("", response_model=PlanOut, status_code=201)
async def create_plan(data: PlanCreate, _admin: AdminUser, primary: PrimaryClient) -> PlanOut:
    return await plan_service.create_plan(primary, data)


@router.patch("/{plan_id}", response_model=PlanOut)
async def update_plan(
    plan_id: int, data: PlanUpdate, _admin: AdminUser, primary: PrimaryClient
) -> PlanOut:
    """Aggiorna parametri/prezzi o disattiva il piano. I piani non si eliminano:
    lo storico abbonamenti li referenzia."""
    return await plan_service.update_plan(primary, plan_id, data)
