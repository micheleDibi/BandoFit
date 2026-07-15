from fastapi import APIRouter

from app.api.deps import ActiveCompanyDep, CurrentUser, PrimaryClient, SecondaryClient
from app.schemas.preferences import PreferencesPayload
from app.services import preferences_service

router = APIRouter(prefix="/me/preferences", tags=["preferences"])


@router.get("", response_model=PreferencesPayload)
async def get_preferences(
    user: CurrentUser, active: ActiveCompanyDep, primary: PrimaryClient
) -> PreferencesPayload:
    """Preferenze di filtro/notifica dell'utente (PERSONALI: anche gli account
    collegati hanno le proprie), scopate sull'azienda attiva per un Advisor."""
    return await preferences_service.get_preferences(primary, user["id"], active)


@router.put("", response_model=PreferencesPayload)
async def save_preferences(
    data: PreferencesPayload,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> PreferencesPayload:
    """Sostituisce il set completo delle preferenze; ogni id è validato
    contro le lookup del catalogo bandi."""
    return await preferences_service.save_preferences(
        primary, secondary, user["id"], active, data
    )
