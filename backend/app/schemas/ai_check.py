"""Contratti dell'AI-check.

Tre famiglie di modelli:
  * Stadio A (`ExtractionResult`) e Stadio B (`MatchingResult`): imposti al
    modello come output strutturato (l'SDK li converte in JSON Schema e valida
    la risposta). Tutti i campi sono OBBLIGATORI (nullable dove serve): lo
    structured output in modalità strict richiede ogni proprietà presente.
  * Report finale (`AiCheckReport`): assemblato in Python dallo scoring
    deterministico — è il contratto unico per DB (`ai_checks.report`),
    risposta API e rendering frontend.
  * DTO API (`AiCheckOut`, `AiChecksResponse`, `AiQuotaOut`).
"""

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------- Stadio A

CategoriaVoce = Literal[
    "soggettivo",
    "territoriale",
    "settoriale",
    "dimensionale",
    "economico",
    "temporale",
    "formale",
    "altro",
]


class CitazioneBando(BaseModel):
    """Ancoraggio al testo del bando: indice di sezione ([META], [S1]..[Sn])
    e testo copiato ALLA LETTERA dall'input."""

    sezione: str
    testo_esatto: str


class RequisitoEstratto(BaseModel):
    id: str
    testo: str
    categoria: CategoriaVoce
    dato_richiesto: str
    citazione: CitazioneBando


class CriterioEstratto(BaseModel):
    id: str
    nome: str
    categoria: CategoriaVoce
    punti_max: float | None
    citazione: CitazioneBando


class GrigliaInfo(BaseModel):
    presente: bool
    fonte: Literal["contenuto", "allegato", "assente"]
    punteggio_max_totale: float | None
    soglia_minima: float | None
    note: str | None


class ExtractionResult(BaseModel):
    requisiti_obbligatori: list[RequisitoEstratto]
    criteri_valutazione: list[CriterioEstratto]
    griglia: GrigliaInfo


# ---------------------------------------------------------------- Stadio B

EsitoRequisito = Literal["soddisfatto", "non_soddisfatto", "dato_mancante"]
EsitoCriterio = Literal[
    "soddisfatto", "parzialmente_soddisfatto", "non_soddisfatto", "dato_mancante"
]


class DatoAzienda(BaseModel):
    """Il dato aziendale usato per il verdetto: nome esatto del campo del
    profilo fornito e valore letto."""

    campo: str
    valore: str


class VerdettoRequisito(BaseModel):
    id: str
    esito: EsitoRequisito
    dato_azienda: DatoAzienda | None
    motivazione: str


class VerdettoCriterio(BaseModel):
    id: str
    esito: EsitoCriterio
    dato_azienda: DatoAzienda | None
    motivazione: str


class PuntoNotevole(BaseModel):
    testo: str
    ref: str | None


class DatoMancante(BaseModel):
    campo: str
    descrizione: str
    ref: str | None


class MatchingResult(BaseModel):
    requisiti: list[VerdettoRequisito]
    criteri: list[VerdettoCriterio]
    punti_di_forza: list[PuntoNotevole]
    punti_di_debolezza: list[PuntoNotevole]
    dati_mancanti: list[DatoMancante]


# ------------------------------------------------------------------ DTO API

EsitoAmmissibilita = Literal["ammissibile", "non_ammissibile", "da_verificare"]
TipoPunteggio = Literal["stima", "euristico"]


class AiCheckRequestIn(BaseModel):
    bando_slug: str = Field(min_length=1, max_length=255)


class AiCheckOut(BaseModel):
    id: str
    bando_id: int
    bando_slug: str
    bando_titolo: str
    status: str
    error_detail: str | None = None
    esito: EsitoAmmissibilita | None = None
    punteggio: int | None = None
    tipo_punteggio: TipoPunteggio | None = None
    model: str | None = None
    extraction_cached: bool = False
    created_at: str
    ready_at: str | None = None
    # Il report completo viaggia solo nel dettaglio e nello storico per bando,
    # non nella lista globale (payload contenuto).
    report: dict | None = None


class AiQuotaOut(BaseModel):
    totale: int
    usati: int
    rimanenti: int
    periodo_inizio: str | None = None
    periodo_fine: str | None = None


class AiChecksResponse(BaseModel):
    editable: bool
    quota: AiQuotaOut
    items: list[AiCheckOut]
    total: int
