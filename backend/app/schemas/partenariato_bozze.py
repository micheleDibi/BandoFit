"""Contratti delle bozze AI dei documenti del partenariato (WP10,
docs/partenariati.md W4).

- Input (`BozzaAvviaIn`): `extra='forbid'`, tipo dal vocabolario chiuso,
  `includi_nome_azienda` booleano STRETTO e falso di default (il nome della
  propria azienda entra nella bozza solo se l'utente lo chiede).
- Uscite a WHITELIST (`extra='forbid'` anche in costruzione): mai
  `input_snapshot`, `company_profile_id`, owner, utenti, esecuzione, costi o
  token. Il `disclaimer` è sempre il testo fisso della piattaforma, mai
  generato dal modello.

Stati della bozza: `pending` (in preparazione, poll sul GET) → `ready`
(titolo, sezioni, note) | `error` (messaggio per l'utente).
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StrictBool

TipoBozza = Literal["lettera_intenti", "nda", "term_sheet"]
StatoBozza = Literal["pending", "ready", "error"]

# Etichette dei tipi (UI, PDF e input del modello).
TIPI_BOZZA: dict[str, str] = {
    "lettera_intenti": "Lettera d'intenti",
    "nda": "Accordo di riservatezza (NDA)",
    "term_sheet": "Term sheet",
}

# Testo FISSO della piattaforma, in testa e a piè del PDF e in ogni risposta:
# mai generato dal modello.
DISCLAIMER = (
    "Bozza generata automaticamente: non costituisce consulenza legale. "
    "Falla rivedere a un professionista prima di firmarla."
)


class _Uscita(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BozzaAvviaIn(BaseModel):
    """POST /partenariati/call/{id}/bozze."""

    model_config = ConfigDict(extra="forbid")

    tipo: TipoBozza
    includi_nome_azienda: StrictBool = False


class SezioneBozzaOut(_Uscita):
    titolo: str
    testo: str


class BozzaOut(_Uscita):
    """Una bozza dell'azienda attiva sulla call. `errore` è il messaggio per
    l'utente (solo con `stato` error); titolo, sezioni e note solo con
    `stato` ready; `avvisi` = ciò che il controllo automatico ha tolto o
    sostituito."""

    id: UUID
    tipo: TipoBozza
    stato: StatoBozza
    avviata_at: datetime
    conclusa_at: datetime | None = None
    errore: str | None = None
    includi_nome_azienda: bool = False
    titolo: str | None = None
    sezioni: list[SezioneBozzaOut] = []
    note_per_l_utente: list[str] = []
    avvisi: list[str] = []
    disclaimer: str = DISCLAIMER


class BozzeOut(_Uscita):
    """GET /partenariati/call/{id}/bozze: le bozze dell'azienda attiva sulla
    call, dalla più recente. `editable` = può avviarne di nuove (titolare);
    il contatore del mese sta in `/me/entitlements.partenariati.bozze_mese`."""

    bozze: list[BozzaOut]
    editable: bool
    disclaimer: str = DISCLAIMER
