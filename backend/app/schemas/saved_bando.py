from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.bando import BandoListItem


class SaveBandoIn(BaseModel):
    bando_slug: str = Field(min_length=1, max_length=255)


class SavedBandoItem(BaseModel):
    """Un bando salvato: la card viva dal catalogo quando disponibile,
    altrimenti il fallback costruito dallo snapshot."""

    bando: BandoListItem
    disponibile: bool
    in_calendario: bool
    salvato_il: datetime


class SavedIdsOut(BaseModel):
    """Id dei bandi salvati: alimenta lo stato dei toggle nelle liste."""

    bando_ids: list[int]
