from fastapi import APIRouter

from app.api.deps import CurrentUser, PrimaryClient
from app.schemas.addon import AddonOut
from app.services import addon_service

router = APIRouter(prefix="/addons", tags=["addons"])


@router.get("", response_model=list[AddonOut])
async def list_addons(_user: CurrentUser, primary: PrimaryClient) -> list[AddonOut]:
    """Add-on attivi, ordinati per `ordering`. A differenza di GET /plans
    (pubblico perché serve alla registrazione) qui serve l'autenticazione:
    il catalogo si vede solo dentro l'app."""
    return await addon_service.list_active_addons(primary)
