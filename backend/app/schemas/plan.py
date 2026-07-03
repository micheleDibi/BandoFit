from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator


class PlanOut(BaseModel):
    id: int
    nome: str
    slug: str
    descrizione: str | None = None
    prezzo_annuale: Decimal
    ai_check: int
    alert_attivo: bool
    alert_giorni_preavviso: int | None = None
    num_account_aziendali: int
    ordering: int
    is_active: bool
    updated_at: datetime | None = None


class PlanCreate(BaseModel):
    nome: str = Field(min_length=1, max_length=100)
    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$")
    descrizione: str | None = None
    prezzo_annuale: Decimal = Field(ge=0)
    ai_check: int = Field(ge=0)
    alert_attivo: bool = False
    alert_giorni_preavviso: int | None = Field(default=None, gt=0)
    num_account_aziendali: int = Field(ge=1)
    ordering: int = 0
    is_active: bool = True

    @model_validator(mode="after")
    def check_alert_coherence(self) -> "PlanCreate":
        if self.alert_attivo and self.alert_giorni_preavviso is None:
            raise ValueError("alert_giorni_preavviso è obbligatorio se gli alert sono attivi")
        return self


class PlanUpdate(BaseModel):
    nome: str | None = Field(default=None, min_length=1, max_length=100)
    descrizione: str | None = None
    prezzo_annuale: Decimal | None = Field(default=None, ge=0)
    ai_check: int | None = Field(default=None, ge=0)
    alert_attivo: bool | None = None
    alert_giorni_preavviso: int | None = Field(default=None, gt=0)
    num_account_aziendali: int | None = Field(default=None, ge=1)
    ordering: int | None = None
    is_active: bool | None = None
