from urllib.parse import quote

from fastapi import APIRouter, Response

from app.api.deps import CurrentUser, OpenapiDep, PrimaryClient, SecondaryClient
from app.schemas.company import CompanyIn, CompanyResponse
from app.schemas.openapi_data import (
    DocumentOut,
    DocumentsResponse,
    DossierResponse,
    ImportIn,
    ImportResult,
)
from app.services import company_service, document_service, openapi_service

router = APIRouter(prefix="/me/company", tags=["company"])


@router.get("", response_model=CompanyResponse)
async def get_company(user: CurrentUser, primary: PrimaryClient) -> CompanyResponse:
    """Dati aziendali: propri per il titolare, della famiglia (sola lettura)
    per un figlio attivo."""
    return await company_service.get_company(primary, user)


@router.put("", response_model=CompanyResponse)
async def save_company(
    data: CompanyIn,
    user: CurrentUser,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> CompanyResponse:
    """Scrittura: bloccata SOLO per i figli attivi (che ereditano i dati della
    famiglia); pending e retrocessi sono account indipendenti con dati propri —
    la coerenza con `editable` di GET è verificata nel service."""
    return await company_service.upsert_company(primary, secondary, user, data)


@router.post("/import", response_model=ImportResult, status_code=201)
async def import_company(
    data: ImportIn,
    user: CurrentUser,
    primary: PrimaryClient,
    secondary: SecondaryClient,
    openapi: OpenapiDep,
) -> ImportResult:
    """Importa la visura completa da openapi.it (IT-full, A PAGAMENTO) e
    compila i campi aziendali vuoti. Protetto da cooldown e lock."""
    return await openapi_service.import_company(
        primary, secondary, openapi, user, data.partita_iva
    )


@router.get("/dossier", response_model=DossierResponse)
async def get_dossier(user: CurrentUser, primary: PrimaryClient) -> DossierResponse:
    """Dossier certificato importato da openapi.it: proprio per il titolare,
    in sola lettura per un figlio attivo."""
    return await openapi_service.get_dossier(primary, user)


@router.post("/documents", response_model=DocumentOut, status_code=201)
async def request_document(
    user: CurrentUser, primary: PrimaryClient, openapi: OpenapiDep
) -> DocumentOut:
    """Richiede la visura camerale ufficiale (A PAGAMENTO, ~2,90–4,90 €).
    Asincrona: torna `pending` se il Registro non ha ancora evaso."""
    return await document_service.request_document(primary, openapi, user)


@router.get("/documents", response_model=DocumentsResponse)
async def list_documents(
    user: CurrentUser, primary: PrimaryClient, openapi: OpenapiDep
) -> DocumentsResponse:
    """Documenti ufficiali dell'azienda; le richieste in corso vengono
    completate qui appena il Registro le evade (controllo gratuito)."""
    return await document_service.list_documents(primary, openapi, user)


@router.get("/documents/{document_id}/file")
async def download_document(
    document_id: str, user: CurrentUser, primary: PrimaryClient
) -> Response:
    """Scarica il PDF del documento (titolare e account collegati)."""
    pdf_bytes, file_name = await document_service.download_document(
        primary, user, document_id
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(file_name)}"
        },
    )
