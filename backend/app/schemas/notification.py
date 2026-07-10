from datetime import datetime

from pydantic import BaseModel, model_validator

from app.schemas.common import Page


class NotificationOut(BaseModel):
    id: int
    tipo: str
    titolo: str
    corpo: str | None = None
    url: str | None = None
    read_at: datetime | None = None
    created_at: datetime


class NotificationsPage(Page[NotificationOut]):
    # Conteggio complessivo delle non lette (non solo della pagina): è il
    # numero sul badge della campanella.
    non_lette: int = 0


class MarkReadIn(BaseModel):
    """`all` per svuotare il badge, `ids` per le letture puntuali."""

    all: bool = False
    ids: list[int] | None = None

    @model_validator(mode="after")
    def check_target(self) -> "MarkReadIn":
        if not self.all and not self.ids:
            raise ValueError("Indica le notifiche da segnare come lette (all o ids)")
        return self
