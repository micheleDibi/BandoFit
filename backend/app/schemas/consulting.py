from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, field_validator


class SlotIn(BaseModel):
    """Orari in UTC (timestamp ISO con offset): la conversione dal fuso
    dell'utente la fa il browser, il backend non assume alcun fuso."""

    inizio: datetime
    fine: datetime

    @field_validator("inizio", "fine")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Orario senza fuso: invia un timestamp ISO con offset (es. Z)")
        return value


class SlotOut(BaseModel):
    id: UUID
    inizio: datetime
    fine: datetime
    # Derivato: esiste una prenotazione confermata (nessuna colonna di stato a DB).
    prenotato: bool = False
