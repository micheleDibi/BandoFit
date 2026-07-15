from fastapi import APIRouter

from app.api.deps import (
    ActiveCompanyDep,
    OpenapiDep,
    PrimaryClient,
    SecondaryClient,
)
from app.schemas.company import CompanyFacetsOut, CompanyIn, CompanyResponse
from app.schemas.openapi_data import (
    DossierResponse,
    ImportConfirmIn,
    ImportIn,
    ImportPreview,
    ImportResult,
)
from app.services import company_service, compatibility, lookup_service, openapi_service

router = APIRouter(prefix="/me/company", tags=["company"])


@router.get("", response_model=CompanyResponse)
async def get_company(active: ActiveCompanyDep, primary: PrimaryClient) -> CompanyResponse:
    """Dati dell'azienda attiva: propri per il titolare, della famiglia (sola
    lettura) per un figlio attivo."""
    return await company_service.get_company(primary, active)


@router.put("", response_model=CompanyResponse)
async def save_company(
    data: CompanyIn,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> CompanyResponse:
    """Scrittura sull'azienda attiva: bloccata SOLO per i figli attivi (che
    ereditano i dati della famiglia); pending e retrocessi sono account
    indipendenti con dati propri. Se l'owner non ha ancora un'azienda, questo
    è il bootstrap della prima."""
    return await company_service.upsert_company(primary, secondary, active, data)


@router.get("/facets", response_model=CompanyFacetsOut)
async def company_facets(
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> CompanyFacetsOut:
    """Cosa l'azienda è DAVVERO, per i filtri: tutte le sedi, non la sola sede
    legale, e le divisioni ATECO secondarie oltre alla principale. Stessa
    funzione che alimenta il badge di compatibilità e l'AI-check.

    Un figlio attivo vede i facet della famiglia, come per i dati aziendali."""
    lookups = await lookup_service.get_lookups(secondary)
    facets = await compatibility.load_company_facets(primary, active, lookups)
    if facets is None:
        return CompanyFacetsOut()
    return CompanyFacetsOut(
        regioni=sorted(facets.regioni_ids),
        ateco=sorted(facets.ateco_ids),
        settori=[facets.settore_id] if facets.settore_id is not None else [],
        beneficiari=sorted(facets.beneficiari_ids),
        sufficiente=facets.sufficiente,
    )


@router.post("/import/preview", response_model=ImportPreview)
async def preview_import(
    data: ImportIn,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
    openapi: OpenapiDep,
) -> ImportPreview:
    """Recupera IT-full da openapi.it (A PAGAMENTO) per l'azienda attiva e
    mostra cosa si sta per importare. NON scrive nulla: il payload resta in
    staging fino alla conferma. Protetto da cooldown e lock; riusa gratis
    un'anteprima già pagata."""
    return await openapi_service.preview_import(
        primary, secondary, openapi, active, data.partita_iva
    )


@router.post("/import/confirm", response_model=ImportResult, status_code=201)
async def confirm_import(
    data: ImportConfirmIn,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> ImportResult:
    """Scrive i dati dell'anteprima sull'azienda attiva e compila i campi
    aziendali vuoti. Nessuna chiamata al provider: gratis, e fuori dal cooldown."""
    return await openapi_service.confirm_import(primary, secondary, active, data.partita_iva)


@router.get("/dossier", response_model=DossierResponse)
async def get_dossier(active: ActiveCompanyDep, primary: PrimaryClient) -> DossierResponse:
    """Dossier certificato importato da openapi.it: proprio per il titolare,
    in sola lettura per un figlio attivo."""
    return await openapi_service.get_dossier(primary, active)
