from fastapi import APIRouter

from app.api.deps import CurrentUser, PrimaryClient
from app.schemas.user import MeOut, ProfileUpdate, SwitchPlanIn
from app.services import user_service

router = APIRouter(prefix="/me", tags=["me"])


@router.get("", response_model=MeOut)
async def get_me(user: CurrentUser, primary: PrimaryClient) -> MeOut:
    return await user_service.get_me(primary, user["id"])


@router.patch("", response_model=MeOut)
async def update_me(data: ProfileUpdate, user: CurrentUser, primary: PrimaryClient) -> MeOut:
    return await user_service.update_profile(primary, user["id"], data)


@router.post("/subscription", response_model=MeOut)
async def switch_my_plan(data: SwitchPlanIn, user: CurrentUser, primary: PrimaryClient) -> MeOut:
    return await user_service.switch_plan(primary, user["id"], data.plan_id)
