from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import AdminUser, PrimaryClient
from app.schemas.common import Page
from app.schemas.user import AdminUserOut, AdminUserUpdate, SwitchPlanIn
from app.services import user_service

router = APIRouter(prefix="/admin/users", tags=["admin"])


@router.get("", response_model=Page[AdminUserOut])
async def list_users(
    _admin: AdminUser,
    primary: PrimaryClient,
    q: str | None = Query(default=None, max_length=200),
    role: Literal["admin", "cliente"] | None = Query(default=None),
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
    data: SwitchPlanIn,
    _admin: AdminUser,
    primary: PrimaryClient,
) -> AdminUserOut:
    return await user_service.admin_switch_user_plan(primary, user_id, data.plan_id)
