from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import AdminUser, PrimaryClient, RevolutDep
from app.schemas.addon import (
    AdminAddonMovementOut,
    AdminGrantAddonIn,
    AdminRevokeAddonIn,
    MyAddonOut,
)
from app.schemas.common import Page
from app.schemas.user import AdminSwitchPlanIn, AdminUserOut, AdminUserUpdate
from app.services import addon_inventory_service, user_service

router = APIRouter(prefix="/admin/users", tags=["admin"])


@router.get("", response_model=Page[AdminUserOut])
async def list_users(
    _admin: AdminUser,
    primary: PrimaryClient,
    q: str | None = Query(default=None, max_length=200),
    role: Literal["admin", "cliente", "progettista"] | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> Page[AdminUserOut]:
    return await user_service.admin_list_users(primary, q, role, page, page_size)


@router.patch("/{user_id}", response_model=AdminUserOut)
async def update_user(
    user_id: UUID,
    data: AdminUserUpdate,
    admin: AdminUser,
    primary: PrimaryClient,
) -> AdminUserOut:
    return await user_service.admin_update_user(primary, user_id, data, admin["id"])


@router.post("/{user_id}/subscription", response_model=AdminUserOut)
async def switch_user_plan(
    user_id: UUID,
    data: AdminSwitchPlanIn,
    admin: AdminUser,
    primary: PrimaryClient,
    revolut: RevolutDep,
) -> AdminUserOut:
    """Cambio piano GRATUITO (nessun pagamento), con motivazione registrata
    nello storico e attore = admin."""
    return await user_service.admin_switch_user_plan(
        primary, user_id, data.plan_id,
        admin_id=admin["id"], motivazione=data.motivazione, revolut=revolut,
    )


@router.get("/{user_id}/addons", response_model=list[MyAddonOut])
async def list_user_addons(
    user_id: UUID, _admin: AdminUser, primary: PrimaryClient
) -> list[MyAddonOut]:
    return await addon_inventory_service.get_inventory(primary, str(user_id))


@router.post("/{user_id}/addons", response_model=AdminAddonMovementOut)
async def grant_user_addon(
    user_id: UUID, data: AdminGrantAddonIn, admin: AdminUser, primary: PrimaryClient
) -> AdminAddonMovementOut:
    """Accredita N unità di un addon a un utente (gratuito, con motivazione e
    audit; compare nello storico acquisti dell'utente come riga a 0 €)."""
    return await addon_inventory_service.grant(primary, admin["id"], str(user_id), data)


@router.post("/{user_id}/addons/{addon_id}/revoke", response_model=AdminAddonMovementOut)
async def revoke_user_addon(
    user_id: UUID, addon_id: int, data: AdminRevokeAddonIn,
    admin: AdminUser, primary: PrimaryClient,
) -> AdminAddonMovementOut:
    """Revoca unità addon (clampate al residuo, mai quelle già consumate)."""
    return await addon_inventory_service.revoke(
        primary, admin["id"], str(user_id), addon_id, data
    )
