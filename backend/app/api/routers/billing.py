from fastapi import APIRouter

from app.api.deps import BillingAccount, OpenapiDep, PrimaryClient
from app.schemas.billing import BillingPrefillOut, BillingProfileIn, BillingProfileOut
from app.services import billing_service

router = APIRouter(prefix="/me/billing-profile", tags=["billing"])


@router.get("", response_model=BillingProfileOut | None)
async def get_billing_profile(
    user: BillingAccount, primary: PrimaryClient
) -> BillingProfileOut | None:
    """L'anagrafica di fatturazione corrente (null se mai compilata)."""
    return await billing_service.get_billing_profile(primary, user["id"])


@router.get("/prefill", response_model=BillingPrefillOut)
async def get_billing_prefill(
    user: BillingAccount, primary: PrimaryClient
) -> BillingPrefillOut:
    """Proposta di precompilazione dai dati dell'azienda (mai persistita)."""
    return await billing_service.get_prefill(primary, user["id"])


@router.put("", response_model=BillingProfileOut)
async def save_billing_profile(
    data: BillingProfileIn,
    user: BillingAccount,
    primary: PrimaryClient,
    openapi: OpenapiDep,
) -> BillingProfileOut:
    """Salva l'anagrafica. Per le aziende con paese UE ≠ HR la P.IVA passa
    dal VIES, NON bloccante: il salvataggio riesce sempre. L'esito sta in
    `vies_valid` — true = reverse charge 0% provato; false = verificata e
    non valida (IVA 25%); null = verifica non riuscita (IVA 25%), si ritenta
    ri-salvando."""
    return await billing_service.save_billing_profile(primary, openapi, user["id"], data)
