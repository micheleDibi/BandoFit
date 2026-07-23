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
    # Appartenenza/visibilità/budget (migration 0031). company_nome è
    # denormalizzato per la UI; aziende_visibili = SOLO le vive.
    company_profile_id: UUID | None = None
    company_nome: str | None = None
    aziende_visibili: list[UUID] = []
    ai_check_budget: int | None = None  # NULL = illimitato
    ai_check_usati: int = 0  # consumi del membro nel ciclo corrente


class FamilyOut(BaseModel):
    limit: int
    used: int  # posti occupati: padre + pending + active
    members: list[FamilyMemberOut]


class InviteMemberIn(BaseModel):
    email: EmailStr
    denominazione: str = Field(min_length=1, max_length=200)
    # Obbligatoria (lato RPC) se il titolare ha più aziende vive; con una sola
    # viene assegnata quella; senza aziende resta NULL.
    company_profile_id: UUID | None = None
    # NULL = illimitato; N >= 0 = tetto per ciclo.
    ai_check_budget: int | None = Field(default=None, ge=0)


class MemberUpdateIn(BaseModel):
    """PATCH del membro (solo i campi presenti vengono applicati; per il
    budget, un null ESPLICITO significa «illimitato»)."""

    company_profile_id: UUID | None = None
    aziende_visibili: list[UUID] | None = None
    ai_check_budget: int | None = Field(default=None, ge=0)


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
