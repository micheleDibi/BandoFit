from datetime import date, datetime, time
from typing import Any, Literal, get_args

from pydantic import BaseModel, field_validator

from app.schemas.common import AtecoItem, LookupItem

# Motivi per cui lo stato di un bando non è certo (contratto DB bandi §4.1).
# Un motivo non cambia lo stato: un bando «aperto · da verificare» resta fra
# gli aperti.
MotivoDaVerificare = Literal[
    "data_apertura_passata",
    "smentito_dalla_fonte",
    "previsione_scaduta",
    "senza_conferma",
    "termine_passato",
]
MOTIVI_DA_VERIFICARE: frozenset[str] = frozenset(get_args(MotivoDaVerificare))


class CompatibilitaDimensione(BaseModel):
    """Dettaglio di un requisito del pre-check. Le voci del bando sono
    alternative: `soddisfatta` è vera con ANCHE UNA SOLA voce in comune.
    `matched`/`totale` (voci in comune / voci elencate dal bando) e
    `matched_ids` servono solo a mostrare il dettaglio, non pesano sul
    punteggio. `nazionale`: il bando è aperto a tutte le regioni."""

    soddisfatta: bool
    matched: int
    totale: int
    matched_ids: list[int] = []
    nazionale: bool = False


class Compatibilita(BaseModel):
    """Punteggio a-priori azienda↔bando: requisiti soddisfatti / valutabili
    (es. 3/4). `punteggio` è la percentuale (per la banda di colore);
    `dimensioni` è il dettaglio per regioni/ateco/settori/beneficiari — una
    dimensione assente non è valutabile e non entra nel denominatore."""

    punteggio: int
    matched: int
    totale: int
    dimensioni: dict[str, CompatibilitaDimensione] | None = None


class BandoListItem(BaseModel):
    id: int
    slug: str
    titolo: str | None = None
    titolo_breve: str | None = None
    descrizione_breve: str | None = None
    # Stato persistito, solo informativo: la verità è `stato_effettivo`
    # (calcolato alla lettura dal catalogo, contratto DB bandi §4).
    stato_bando: str | None = None
    stato_effettivo: str | None = None
    # Perché lo stato non è certo (§4.1), oppure None: stato certo.
    stato_da_verificare: MotivoDaVerificare | None = None
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

    @field_validator("stato_da_verificare", mode="before")
    @classmethod
    def _motivo_noto(cls, value: Any) -> str | None:
        """Tollerante: un motivo nuovo o un valore non stringa diventano None
        (stato mostrato senza dubbio), mai un errore di validazione."""
        return value if isinstance(value, str) and value in MOTIVI_DA_VERIFICARE else None


OrigineLink = Literal["candidatura", "link_candidatura", "fonte_ufficiale", "portale", "link_bando"]


class LinkScheda(BaseModel):
    """Un pulsante della scheda, già filtrato dal backend. `host` è
    l'etichetta da mostrare; `origine` dice da quale fonte viene l'URL."""

    url: str
    host: str | None = None
    origine: OrigineLink


class AllegatoScheda(BaseModel):
    """Un allegato della scheda, già filtrato e senza doppioni. `etichetta`
    non è mai vuota (etichetta del catalogo, poi nome del file, poi
    «Allegato»); `tipo`: `atto`, `allegato` o il tipo dichiarato dal
    catalogo; `formato`: es. `pdf`, se noto."""

    url: str
    etichetta: str
    tipo: str | None = None
    formato: str | None = None


class BandoDetail(BandoListItem):
    area_geografica: str | None = None
    tematica: list[str] = []
    ora_apertura: time | None = None
    ora_scadenza: time | None = None
    # True solo se la data ha una prova su un dominio ufficiale.
    data_pubblicazione_verificata: bool | None = None
    data_apertura_verificata: bool | None = None
    data_scadenza_verificata: bool | None = None
    contenuto: dict[str, Any] | None = None
    # Pulsante principale e pulsante «Fonte ufficiale» (la UI mostra il
    # secondo solo se diverso dal primo), calcolati dal backend.
    cta: LinkScheda | None = None
    link_fonte: LinkScheda | None = None
    allegati: list[AllegatoScheda] = []
    # Fonte ufficiale trovata dal produttore del catalogo. Tipi `str` e non
    # Literal: un valore nuovo di tipo/stato non deve rompere la validazione.
    fonte_ufficiale_url: str | None = None
    fonte_ufficiale_host: str | None = None
    fonte_ufficiale_tipo: str | None = None  # ente | portale_pubblico
    fonte_ufficiale_stato: str | None = None  # trovata | in_verifica | non_trovata
    fonte_ufficiale_verificata_at: datetime | None = None
    fonte_ufficiale_e_atto: bool | None = None
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
