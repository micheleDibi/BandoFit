from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

FamilyMemberStatus = Literal["pending", "active", "demoted", "removed", "declined"]


class FamilyMemberOut(BaseModel):
    id: UUID  # id della membership (non dell'utente)
    member_id: UUID
    denominazione: str
    email: str
    status: FamilyMemberStatus
    invite_kind: Literal["new_user", "existing_user"]
    invited_at: datetime
    joined_at: datetime | None = None
    demoted_at: datetime | None = None


class FamilyOut(BaseModel):
    limit: int
    used: int  # posti occupati: padre + pending + active
    members: list[FamilyMemberOut]


class InviteMemberIn(BaseModel):
    email: EmailStr
    denominazione: str = Field(min_length=1, max_length=200)


class InviteMemberOut(BaseModel):
    family: FamilyOut
    email_sent: bool


class InvitationOut(BaseModel):
    id: UUID  # id della membership
    denominazione: str
    parent_display_name: str
    invited_at: datetime


class MeFamilyOut(BaseModel):
    role: Literal["parent", "child"]
    # valorizzati per il padre
    limit: int | None = None
    used: int | None = None
    # valorizzati per il figlio
    status: FamilyMemberStatus | None = None
    denominazione: str | None = None
    parent_display_name: str | None = None


class PlanSwitchAdjustment(BaseModel):
    """Effetti collaterali di un downgrade sulla famiglia."""

    demoted: list[dict] = []
    revoked_pending: list[dict] = []
