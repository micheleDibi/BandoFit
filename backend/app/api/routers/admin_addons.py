from fastapi import APIRouter

from app.api.deps import AdminUser, PrimaryClient
from app.schemas.addon import AddonCreate, AddonOut, AddonUpdate
from app.services import addon_service

router = APIRouter(prefix="/admin/addons", tags=["admin"])


@router.get("", response_model=list[AddonOut])
async def list_addons(_admin: AdminUser, primary: PrimaryClient) -> list[AddonOut]:
    """Tutti gli add-on, inclusi quelli disattivati."""
    return await addon_service.list_all_addons(primary)


@router.post("", response_model=AddonOut, status_code=201)
async def create_addon(data: AddonCreate, _admin: AdminUser, primary: PrimaryClient) -> AddonOut:
    return await addon_service.create_addon(primary, data)


@router.patch("/{addon_id}", response_model=AddonOut)
async def update_addon(
    addon_id: int, data: AddonUpdate, _admin: AdminUser, primary: PrimaryClient
) -> AddonOut:
    """Aggiorna dati/prezzo o disattiva l'add-on. Gli add-on non si eliminano:
    lo slug è l'identificativo stabile a cui si agganceranno le funzionalità."""
    return await addon_service.update_addon(primary, addon_id, data)
