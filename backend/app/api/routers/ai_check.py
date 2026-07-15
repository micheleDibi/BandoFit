from fastapi import APIRouter, Query

from app.api.deps import ActiveCompanyDep, AiDep, CurrentUser, PrimaryClient, SecondaryClient
from app.schemas.ai_check import (
    AiCheckOut,
    AiCheckRequestIn,
    AiChecksResponse,
    AiQuotaOut,
)
from app.services import ai_check_service

router = APIRouter(prefix="/me/ai-checks", tags=["ai-check"])


@router.post("", response_model=AiCheckOut, status_code=201)
async def request_ai_check(
    payload: AiCheckRequestIn,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
    ai: AiDep,
) -> AiCheckOut:
    """Avvia l'analisi di compatibilità azienda ↔ bando (consuma 1 AI-check
    del piano). L'analisi gira in background: lo stato si segue con la GET."""
    return await ai_check_service.request_check(
        primary, secondary, ai, user, active, payload.bando_slug
    )


@router.get("", response_model=AiChecksResponse)
async def list_ai_checks(
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    bando_slug: str | None = Query(default=None, max_length=255),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
) -> AiChecksResponse:
    return await ai_check_service.list_checks(primary, active, bando_slug, page, page_size)


@router.get("/quota", response_model=AiQuotaOut)
async def ai_check_quota(user: CurrentUser, primary: PrimaryClient) -> AiQuotaOut:
    return await ai_check_service.quota_for(primary, user)


@router.get("/{check_id}", response_model=AiCheckOut)
async def get_ai_check(
    check_id: str, active: ActiveCompanyDep, primary: PrimaryClient
) -> AiCheckOut:
    return await ai_check_service.get_check(primary, active, check_id)
