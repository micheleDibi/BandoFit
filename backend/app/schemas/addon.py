from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.plan import TipoPrezzo


class AddonOut(BaseModel):
    id: int
    nome: str
    slug: str
    descrizione: str | None = None
    prezzo: Decimal
    tipo_prezzo: TipoPrezzo = "importo"
    etichetta_prezzo: str | None = None
    ordering: int
    is_active: bool
    updated_at: datetime | None = None


class AddonCreate(BaseModel):
    nome: str = Field(min_length=1, max_length=100)
    # Identificativo STABILE: aggancerà le funzionalità future (mai modificabile).
    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$")
    descrizione: str | None = None
    prezzo: Decimal = Field(ge=0)
    tipo_prezzo: TipoPrezzo = "importo"
    etichetta_prezzo: str | None = Field(default=None, max_length=100)
    ordering: int = 0
    is_active: bool = True


class AddonUpdate(BaseModel):
    # Come per i piani: lo slug è immutabile e non compare nell'update.
    nome: str | None = Field(default=None, min_length=1, max_length=100)
    descrizione: str | None = None
    prezzo: Decimal | None = Field(default=None, ge=0)
    tipo_prezzo: TipoPrezzo | None = None
    etichetta_prezzo: str | None = Field(default=None, max_length=100)
    ordering: int | None = None
    is_active: bool | None = None
