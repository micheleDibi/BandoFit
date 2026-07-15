"""Gestione delle aziende gestite (piano Advisor, multi-azienda).

Owner-only: un account collegato a una famiglia non gestisce aziende (in v1
Advisor e collegati sono mutuamente esclusivi), quindi tutte le rotte passano
da `ParentUser`. Il limite e la corsa sulla creazione li impone la RPC
`fn_create_company` (0023)."""

from uuid import UUID

from fastapi import APIRouter

from app.api.deps import ParentUser, PrimaryClient
from app.schemas.company import CompaniesOut, CompanyCreate, CompanySummary
from app.services import company_service

router = APIRouter(prefix="/me/aziende", tags=["aziende"])


@router.get("", response_model=CompaniesOut)
async def list_aziende(user: ParentUser, primary: PrimaryClient) -> CompaniesOut:
    """Aziende vive gestite, con il limite effettivo del piano e quante ne sono
    in uso. La prima (più vecchia) è quella attiva di default."""
    return await company_service.list_companies(primary, user["id"])


@router.post("", response_model=CompanySummary, status_code=201)
async def create_azienda(
    data: CompanyCreate, user: ParentUser, primary: PrimaryClient
) -> CompanySummary:
    """Crea una nuova azienda (ragione sociale + P.IVA obbligatorie). `409` se
    si è raggiunto il limite del piano, `400` se la P.IVA non è valida."""
    return await company_service.create_company(primary, user["id"], data)


@router.delete("/{company_id}", status_code=204)
async def delete_azienda(
    company_id: UUID, user: ParentUser, primary: PrimaryClient
) -> None:
    """Soft-delete di un'azienda gestita: i dati restano (recuperabili), ma
    l'azienda esce da switch/alert/export. `404` se non è dell'owner o già rimossa."""
    await company_service.soft_delete_company(primary, user["id"], str(company_id))
