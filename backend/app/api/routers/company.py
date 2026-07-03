from fastapi import APIRouter

from app.api.deps import CurrentUser, ParentUser, PrimaryClient, SecondaryClient
from app.schemas.company import CompanyIn, CompanyResponse
from app.services import company_service

router = APIRouter(prefix="/me/company", tags=["company"])


@router.get("", response_model=CompanyResponse)
async def get_company(user: CurrentUser, primary: PrimaryClient) -> CompanyResponse:
    """Dati aziendali: propri per il titolare, della famiglia (sola lettura)
    per un figlio attivo."""
    return await company_service.get_company(primary, user)


@router.put("", response_model=CompanyResponse)
async def save_company(
    data: CompanyIn,
    parent: ParentUser,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> CompanyResponse:
    return await company_service.upsert_company(primary, secondary, parent, data)
