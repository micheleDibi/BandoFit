from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.plan import TipoPrezzo

# consumabile = unità a quantità (si compra N volte, si consuma);
# permanente = possesso binario (0 o 1). Immutabile come lo slug (migration 0028).
TipoFruizione = Literal["consumabile", "permanente"]

# Risorsa entitlement estesa dall'addon (migration 0030): il motore chiavizza
# su questa, mai sullo slug. Si assegna solo via migration (non dalla console).
RisorsaAddon = Literal["seats", "companies"]


class AddonOut(BaseModel):
    id: int
    nome: str
    slug: str
    descrizione: str | None = None
    prezzo: Decimal
    tipo_prezzo: TipoPrezzo = "importo"
    tipo_fruizione: TipoFruizione = "consumabile"
    risorsa: RisorsaAddon | None = None
    etichetta_prezzo: str | None = None
    ordering: int
    is_active: bool
    updated_at: datetime | None = None
    # Acquistabilità per l'UTENTE della richiesta (0030, solo tipo_prezzo
    # 'importo'): un collegato attivo non compra dal checkout; un allocativo
    # richiede un piano la cui base abiliti la risorsa. Il gate vero resta
    # server-side nel checkout: qui è il segnale per la CTA.
    acquistabile: bool = True
    motivo_non_acquistabile: Literal["solo_titolare", "piano_non_idoneo"] | None = None


class AddonCreate(BaseModel):
    nome: str = Field(min_length=1, max_length=100)
    # Identificativo STABILE: aggancerà le funzionalità future (mai modificabile).
    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$")
    descrizione: str | None = None
    prezzo: Decimal = Field(ge=0)
    tipo_prezzo: TipoPrezzo = "importo"
    # Come lo slug, il tipo di fruizione è fissato alla creazione (non in Update).
    tipo_fruizione: TipoFruizione = "consumabile"
    etichetta_prezzo: str | None = Field(default=None, max_length=100)
    ordering: int = 0
    is_active: bool = True


class MyAddonOut(BaseModel):
    """Una voce dell'inventario addon dell'utente. Dalla 0030 include anche i
    totali storici dal ledger (per la pagina «I miei addon»): `acquistate` =
    accrediti (acquisti + grant + rimborsi), `consumate` = soli consumi — le
    revoche admin riducono `quantita` senza contare come consumo."""

    addon_id: int
    slug: str
    nome: str
    descrizione: str | None = None
    tipo_fruizione: TipoFruizione
    risorsa: RisorsaAddon | None = None
    quantita: int
    acquistate: int = 0
    consumate: int = 0
    updated_at: datetime | None = None


class AddonLedgerEntryOut(BaseModel):
    tipo: Literal["purchase", "admin_grant", "consume", "refund", "admin_revoke"]
    delta: int
    note: str | None = None
    created_at: datetime


class AdminGrantAddonIn(BaseModel):
    addon_id: int
    quantita: int = Field(default=1, ge=1, le=100)
    motivazione: str = Field(min_length=1, max_length=500)


class AdminRevokeAddonIn(BaseModel):
    quantita: int = Field(default=1, ge=1, le=100)
    motivazione: str = Field(min_length=1, max_length=500)


class AdminAddonMovementOut(BaseModel):
    """Esito di un grant/revoca admin."""

    purchase_id: str | None = None
    quantita_residua: int
    quantita_revocata: int | None = None


class AddonUpdate(BaseModel):
    # Come per i piani: lo slug è immutabile e non compare nell'update.
    nome: str | None = Field(default=None, min_length=1, max_length=100)
    descrizione: str | None = None
    prezzo: Decimal | None = Field(default=None, ge=0)
    tipo_prezzo: TipoPrezzo | None = None
    etichetta_prezzo: str | None = Field(default=None, max_length=100)
    ordering: int | None = None
    is_active: bool | None = None
