"""Schemi del checkout e dello storico acquisti (migration 0026)."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import Page


class CheckoutTargetIn(BaseModel):
    """Uno e uno solo tra piano e addon."""

    plan_slug: str | None = Field(default=None, max_length=100)
    addon_slug: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def _uno_solo(self) -> "CheckoutTargetIn":
        if bool(self.plan_slug) == bool(self.addon_slug):
            raise ValueError("indica un piano oppure un addon")
        return self


class CheckoutIn(CheckoutTargetIn):
    # Rinnovo automatico: scelta dell'utente al checkout (default on, dichiarato
    # accanto al prezzo). Ha senso solo per i piani; ignorato sugli addon.
    auto_renew: bool = True


class CheckoutPreviewOut(BaseModel):
    kind: Literal["piano", "addon"]
    oggetto_slug: str
    oggetto_nome: str
    listino_cents: int
    credito_cents: int
    imponibile_cents: int
    iva_cents: int
    iva_aliquota: str
    natura_iva: str | None = None
    totale_cents: int
    valuta: str = "EUR"
    scadenza_risultante: date | None = None
    dettaglio: dict


class CheckoutOut(BaseModel):
    purchase_id: str
    revolut_order_token: str
    checkout_url: str | None = None
    totale_cents: int
    valuta: str = "EUR"


class PurchaseOut(BaseModel):
    id: str
    kind: Literal["piano", "rinnovo", "addon", "cambio_admin"]
    status: Literal["in_attesa", "pagato", "fallito", "scaduto", "annullato", "gratuito"]
    oggetto_slug: str
    oggetto_nome: str
    descrizione: str
    imponibile_cents: int
    iva_cents: int
    totale_cents: int
    iva_aliquota: str
    natura_iva: str | None = None
    valuta: str
    decline_reason: str | None = None
    motivazione: str | None = None  # solo cambio_admin
    created_at: datetime
    paid_at: datetime | None = None


class PurchasesPage(Page[PurchaseOut]):
    pass


class DowngradeIn(BaseModel):
    plan_slug: str = Field(max_length=100)


class AutoRenewIn(BaseModel):
    enabled: bool


class ScheduledChangeOut(BaseModel):
    to_plan_slug: str
    to_plan_nome: str
    effective_date: date
    motivo: str


class SavedMethodOut(BaseModel):
    presente: bool
    label: str | None = None


class AddMethodOut(BaseModel):
    """Ordine a 0 amount per salvare un metodo senza acquisto."""

    revolut_order_token: str


class SubscriptionMgmtOut(BaseModel):
    auto_renew: bool
    data_scadenza: date | None = None
    metodo: SavedMethodOut
    cambio_programmato: ScheduledChangeOut | None = None
