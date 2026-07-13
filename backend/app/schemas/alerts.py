from datetime import date, datetime

from pydantic import BaseModel


class AlertSettingsOut(BaseModel):
    abilitati: bool
    # Il piano EFFETTIVO (per i collegati attivi: quello del titolare).
    piano_include_alert: bool
    ritardo_giorni: int | None = None


class AlertSettingsIn(BaseModel):
    abilitati: bool


class AlertRunOut(BaseModel):
    """Riga del registro run (osservabilità)."""

    giorno: date
    started_at: datetime | None = None
    finished_at: datetime | None = None
    esito: str | None = None
    bandi_candidati: int | None = None
    destinatari: int | None = None
    email_inviate: int | None = None
    email_fallite: int | None = None
    dettagli: dict = {}


class AlertRunRiepilogoOut(BaseModel):
    """Esito immediato di una run lanciata dall'admin."""

    giorno: date
    esito: str
    bandi_candidati: int
    destinatari: int
    email_inviate: int
    email_fallite: int
    dettagli: dict = {}
