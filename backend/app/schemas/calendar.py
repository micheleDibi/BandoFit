"""Contratti del calendario personale.

Date e orari sono di CALENDARIO ITALIANO (wall-clock): tipi `date`/`time`
senza fuso, mostrati dal client così come sono — coerenti con le scadenze
del catalogo bandi.
"""

from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


def _titolo_pulito(value: str) -> str:
    """Un titolo di soli spazi passerebbe min_length e poi violerebbe il CHECK
    del DB (→ 502): va respinto qui come 400/422."""
    stripped = value.strip()
    if not stripped:
        raise ValueError("Il titolo è obbligatorio")
    return stripped


def _data_in_range(value: date) -> date:
    """Il calendario copre il 2000-2100 (stessi limiti della GET): una data
    fuori intervallo creerebbe un evento invisibile che consuma il limite."""
    if not 2000 <= value.year <= 2100:
        raise ValueError("La data deve essere compresa tra il 2000 e il 2100")
    return value


class CalendarEventIn(BaseModel):
    """Creazione/validazione di un evento personale. È anche l'unica fonte
    delle regole di coerenza sugli orari (riusata dal PATCH)."""

    titolo: str = Field(min_length=1, max_length=200)
    data: date
    tutto_il_giorno: bool = True
    ora_inizio: time | None = None
    ora_fine: time | None = None
    note: str | None = Field(default=None, max_length=2000)

    _titolo = field_validator("titolo")(_titolo_pulito)
    _data = field_validator("data")(_data_in_range)

    @model_validator(mode="after")
    def _coerenza_orari(self) -> "CalendarEventIn":
        if self.tutto_il_giorno:
            # Tollerante: un evento "tutto il giorno" non ha orari.
            self.ora_inizio = None
            self.ora_fine = None
            return self
        if self.ora_inizio is None:
            raise ValueError("Indica l'ora di inizio (o segna l'evento come tutto il giorno)")
        if self.ora_fine is not None and self.ora_fine <= self.ora_inizio:
            raise ValueError("L'ora di fine deve essere successiva a quella di inizio")
        return self


class CalendarEventUpdate(BaseModel):
    """PATCH: tutti i campi opzionali (exclude_unset decide cosa cambia)."""

    titolo: str | None = Field(default=None, min_length=1, max_length=200)
    data: date | None = None
    tutto_il_giorno: bool | None = None
    ora_inizio: time | None = None
    ora_fine: time | None = None
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("titolo")
    @classmethod
    def _titolo(cls, value: str | None) -> str | None:
        return None if value is None else _titolo_pulito(value)

    @field_validator("data")
    @classmethod
    def _data(cls, value: date | None) -> date | None:
        return None if value is None else _data_in_range(value)


class CalendarBandoIn(BaseModel):
    bando_slug: str = Field(min_length=1, max_length=255)


class CalendarEventOut(BaseModel):
    id: str
    titolo: str
    data: date
    tutto_il_giorno: bool
    ora_inizio: time | None = None
    ora_fine: time | None = None
    note: str | None = None
    tipo: Literal["personale", "bando"]
    bando_id: int | None = None
    bando_slug: str | None = None
    created_at: datetime
    updated_at: datetime


class CalendarEventsOut(BaseModel):
    items: list[CalendarEventOut]
