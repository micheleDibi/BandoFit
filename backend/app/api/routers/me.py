from fastapi import APIRouter

from app.api.deps import CurrentUser, OpenapiDep, PrimaryClient
from app.schemas.user import MeOut, ProfileUpdate, SwitchPlanIn, VerifyCfIn, VerifyCfOut
from app.services import openapi_service, user_service

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


@router.post("/verify-cf", response_model=VerifyCfOut)
async def verify_cf(
    data: VerifyCfIn, user: CurrentUser, primary: PrimaryClient, openapi: OpenapiDep
) -> VerifyCfOut:
    """Verifica il codice fiscale all'Anagrafe Tributaria (openapi.it,
    A PAGAMENTO ~0,05 €). Idempotente sullo stesso CF già verificato."""
    result = await openapi_service.verify_cf(primary, openapi, user, data.codice_fiscale)
    return VerifyCfOut(**result)
