from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.plan import PlanOut


class SubscriptionOut(BaseModel):
    id: UUID
    status: str
    data_inizio: date
    data_scadenza: date
    plan: PlanOut


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


class ProfileUpdate(BaseModel):
    nome: str | None = Field(default=None, max_length=100)
    cognome: str | None = Field(default=None, max_length=100)
    azienda: str | None = Field(default=None, max_length=200)
    telefono: str | None = Field(default=None, max_length=50)


class SwitchPlanIn(BaseModel):
    plan_id: int


class AdminUserOut(BaseModel):
    profile: ProfileOut
    subscription: SubscriptionOut | None = None


class AdminUserUpdate(BaseModel):
    role: Literal["admin", "cliente"] | None = None
    is_active: bool | None = None
