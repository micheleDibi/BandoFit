from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, OpenapiDep, PrimaryClient
from app.schemas.addon import AddonLedgerEntryOut, MyAddonOut
from app.schemas.user import MeOut, ProfileUpdate, SwitchPlanIn, VerifyCfIn, VerifyCfOut
from app.services import addon_inventory_service, openapi_service, user_service

router = APIRouter(prefix="/me", tags=["me"])


@router.get("/addons", response_model=list[MyAddonOut])
async def my_addons(user: CurrentUser, primary: PrimaryClient) -> list[MyAddonOut]:
    """L'inventario addon dell'utente (unità possedute per addon)."""
    return await addon_inventory_service.get_inventory(primary, user["id"])


@router.get("/addons/ledger", response_model=list[AddonLedgerEntryOut])
async def my_addons_ledger(
    user: CurrentUser,
    primary: PrimaryClient,
    addon_id: int | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[AddonLedgerEntryOut]:
    """Storico movimenti addon dell'utente (acquisti, consumi, accrediti)."""
    return await addon_inventory_service.get_ledger(primary, user["id"], addon_id, limit)


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
