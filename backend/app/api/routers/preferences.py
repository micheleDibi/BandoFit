from fastapi import APIRouter

from app.api.deps import CurrentUser, PrimaryClient, SecondaryClient
from app.schemas.preferences import PreferencesPayload
from app.services import preferences_service

router = APIRouter(prefix="/me/preferences", tags=["preferences"])


@router.get("", response_model=PreferencesPayload)
async def get_preferences(user: CurrentUser, primary: PrimaryClient) -> PreferencesPayload:
    """Preferenze di filtro/notifica dell'utente (PERSONALI: anche gli account
    collegati hanno le proprie)."""
    return await preferences_service.get_preferences(primary, user["id"])


@router.put("", response_model=PreferencesPayload)
async def save_preferences(
    data: PreferencesPayload,
    user: CurrentUser,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> PreferencesPayload:
    """Sostituisce il set completo delle preferenze; ogni id è validato
    contro le lookup del catalogo bandi."""
    return await preferences_service.save_preferences(primary, secondary, user["id"], data)
