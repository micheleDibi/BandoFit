from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.family import MeFamilyOut, PlanSwitchAdjustment
from app.schemas.plan import PlanOut


class SubscriptionOut(BaseModel):
    id: UUID
    status: str
    data_inizio: date
    data_scadenza: date
    plan: PlanOut
    # True se l'abbonamento è quello del titolare della famiglia (figlio attivo).
    inherited: bool = False


class ProfileOut(BaseModel):
    id: UUID
    email: str
    nome: str | None = None
    cognome: str | None = None
    azienda: str | None = None
    telefono: str | None = None
    role: Literal["admin", "cliente"]
    is_active: bool
    created_at: datetime


class MeOut(BaseModel):
    profile: ProfileOut
    subscription: SubscriptionOut | None = None
    family: MeFamilyOut | None = None
    # Presente solo nella risposta di un cambio piano che ha causato retrocessioni.
    plan_switch_adjustment: PlanSwitchAdjustment | None = None


class ProfileUpdate(BaseModel):
    nome: str | None = Field(default=None, max_length=100)
    cognome: str | None = Field(default=None, max_length=100)
    azienda: str | None = Field(default=None, max_length=200)
    telefono: str | None = Field(default=None, max_length=50)


class SwitchPlanIn(BaseModel):
    plan_id: int


class AdminFamilyInfo(BaseModel):
    type: Literal["parent", "child"]
    # figlio
    status: str | None = None
    parent_email: str | None = None
    # padre
    members_count: int | None = None


class AdminUserOut(BaseModel):
    profile: ProfileOut
    subscription: SubscriptionOut | None = None
    family: AdminFamilyInfo | None = None


class AdminUserUpdate(BaseModel):
    role: Literal["admin", "cliente"] | None = None
    is_active: bool | None = None
