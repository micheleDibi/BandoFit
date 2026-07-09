from datetime import date
from typing import Any

from pydantic import BaseModel

from app.schemas.common import AtecoItem, LookupItem


class Compatibilita(BaseModel):
    """Punteggio a-priori azienda↔bando: relazioni in comune / totali (es.
    18/23). `punteggio` è la percentuale (per la banda di colore); `dimensioni`
    è il dettaglio per regioni/ateco/settori/beneficiari."""

    punteggio: int
    matched: int
    totale: int
    dimensioni: dict[str, dict[str, int]] | None = None


class BandoListItem(BaseModel):
    id: int
    slug: str
    titolo: str | None = None
    titolo_breve: str | None = None
    descrizione_breve: str | None = None
    stato_bando: str | None = None
    livello: str | None = None
    data_pubblicazione: date | None = None
    data_apertura: date | None = None
    data_scadenza: date | None = None
    importo_totale_eur: int | None = None
    importo_max_per_progetto_eur: int | None = None
    ente_erogatore: str | None = None
    tipologia: LookupItem | None = None
    modalita_erogazione: LookupItem | None = None
    regioni: list[LookupItem] = []
    # Calcolato dinamicamente (mai persistito); None se profilo insufficiente.
    compatibilita: Compatibilita | None = None


class BandoDetail(BandoListItem):
    area_geografica: str | None = None
    tematica: list[str] = []
    link_bando: str | None = None
    link_candidatura: str | None = None
    contenuto: dict[str, Any] | None = None
    allegati: list[Any] = []
    programma: LookupItem | None = None
    settori: list[LookupItem] = []
    beneficiari: list[LookupItem] = []
    codici_ateco: list[AtecoItem] = []


class LookupsOut(BaseModel):
    regioni: list[LookupItem]
    settori: list[LookupItem]
    beneficiari: list[LookupItem]
    codici_ateco: list[AtecoItem]
    tipologie_bando: list[LookupItem]
    modalita_erogazione: list[LookupItem]
    programmi: list[LookupItem]
