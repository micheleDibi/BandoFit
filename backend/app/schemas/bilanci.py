"""Schemi dei bilanci strutturati (WP1): risposta di GET /me/company/bilanci,
blocco `bilanci` dell'anteprima di import e vocabolari condivisi.

I valori escono come numeri JSON (float) già fusi per campo: la precedenza
tra le fonti (xbrl > it_full > it_advanced) la decide il DB, qui si
espone soltanto il risultato con la fonte di ogni campo. I payload grezzi
(`advanced_raw`, XBRL) non passano mai da questi schemi.
"""

from typing import Literal

from pydantic import BaseModel, Field

FonteBilancio = Literal["xbrl", "it_full", "it_advanced"]

# Motivo per cui lo storico dei bilanci manca o non è stato richiesto (stesso
# vocabolario dei CHECK di company_import_drafts e company_financials_stato).
MotivoBilanci = Literal[
    "nessun_bilancio",
    "forma_senza_bilancio",
    "errore_provider",
    "esito_incerto",
    "tempo_insufficiente",
    "dati_non_corrispondenti",
    "non_richiesto",
    "piva_diversa",
]

# Esito dell'ultimo tentativo IT-advanced (colonna advanced_esito).
EsitoStoricoBilanci = Literal["ok", "non_disponibili", "errore", "timeout", "saltato", "mismatch"]

StatoBilanci = Literal["disponibili", "non_disponibili", "mai_richiesti"]

TipoBilancio = Literal["ordinario", "abbreviato", "micro", "ignoto"]

UnitaIndicatore = Literal["percentuale", "euro", "rapporto"]


class EsercizioOut(BaseModel):
    """Riga fusa di un esercizio (company_financials). `fonti` elenca solo i
    campi valorizzati."""

    anno: int
    data_chiusura: str | None = None  # YYYY-MM-DD
    tipo_bilancio: TipoBilancio = "ignoto"
    fatturato: float | None = None
    valore_produzione: float | None = None
    risultato_esercizio: float | None = None
    patrimonio_netto: float | None = None
    capitale_sociale: float | None = None
    totale_attivo: float | None = None
    debiti_totali: float | None = None
    disponibilita_liquide: float | None = None
    ebitda: float | None = None
    ebit: float | None = None
    cash_flow: float | None = None
    oneri_finanziari: float | None = None
    dipendenti: float | None = None
    costo_personale: float | None = None
    retribuzione_media_lorda: float | None = None
    fonti: dict[str, FonteBilancio] = Field(default_factory=dict)


class IndicatoreOut(BaseModel):
    chiave: str
    etichetta: str
    valore: float | None = None
    unita: UnitaIndicatore
    anni: list[int] = Field(default_factory=list)
    formula: str
    motivo_mancanza: str | None = None


class FasceOut(BaseModel):
    """Fasce dell'azienda (codici, mai importi): pronte per i terzi da WP4.
    In WP1 le vede solo il titolare."""

    fatturato: str | None = None
    patrimonio_netto: str | None = None
    dipendenti: str | None = None
    trend_fatturato: str | None = None
    anno_riferimento: int | None = None


class BilanciOut(BaseModel):
    """Stato e contenuto dei bilanci dell'azienda attiva.

    `stato` è derivato: `disponibili` se esiste almeno un esercizio,
    altrimenti `non_disponibili` se c'è stato un tentativo, altrimenti
    `mai_richiesti`. `storico_esito`/`motivo` descrivono l'ultimo tentativo
    di recupero dello storico (IT-advanced); `recuperabile_da` è il primo
    istante in cui il titolare può ritentare (fine del cooldown)."""

    editable: bool
    stato: StatoBilanci
    motivo: MotivoBilanci | None = None
    storico_esito: EsitoStoricoBilanci | None = None
    ultimo_tentativo_at: str | None = None
    recuperabile_da: str | None = None
    sandbox: bool | None = None
    esercizi: list[EsercizioOut] = Field(default_factory=list)  # anno crescente
    indicatori: list[IndicatoreOut] = Field(default_factory=list)
    fasce: FasceOut | None = None


class ImportPreviewBilanci(BaseModel):
    """Blocco `bilanci` dell'anteprima di import. Il default descrive un
    draft senza tentativo IT-advanced (draft vecchi o riusati)."""

    stato: Literal["disponibili", "non_disponibili"] = "non_disponibili"
    motivo: MotivoBilanci | None = "non_richiesto"
    anni: list[int] = Field(default_factory=list)
