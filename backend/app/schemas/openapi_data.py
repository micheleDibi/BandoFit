"""Schemi per l'import dati openapi.it e il dossier aziendale.

Il dossier è volutamente un dict annidato (sezioni → campi): la struttura è
definita da openapi_mapping.build_dossier e il frontend nasconde i campi
nulli; tipizzarlo campo per campo aggiungerebbe solo rigidità sui ~1300
possibili campi del payload.
"""

from pydantic import BaseModel, Field, field_validator

from app.schemas.company import CompanyResponse


class ImportIn(BaseModel):
    """Body dell'import: P.IVA esplicita, oppure quella già salvata nei dati aziendali."""

    partita_iva: str | None = Field(default=None, max_length=20)

    @field_validator("partita_iva")
    @classmethod
    def normalize(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().upper().removeprefix("IT").replace(" ", "")
        return cleaned or None


class PersonOut(BaseModel):
    kind: str
    nome: str | None = None
    cognome: str | None = None
    denominazione: str | None = None
    codice_fiscale: str | None = None
    data_nascita: str | None = None
    luogo_nascita: str | None = None
    genere: str | None = None
    ruoli: list[dict] = []
    is_legale_rappresentante: bool = False
    quota_percentuale: float | None = None
    data_inizio_carica: str | None = None


class AutofillOut(BaseModel):
    applied: list[str] = []
    conflicts: list[dict] = []


class SuggestionsOut(BaseModel):
    codici_ateco: list[dict] = []


class ImportResult(BaseModel):
    company: CompanyResponse
    dossier: dict
    people: list[PersonOut] = []
    autofill: AutofillOut
    suggestions: SuggestionsOut
    fetched_at: str
    sandbox: bool


class ImportConfirmIn(ImportIn):
    """Conferma dell'anteprima. La P.IVA è una guardia: deve combaciare con
    quella del draft, o si scriverebbero i dati di un'altra azienda. Stessa
    normalizzazione dell'anteprima (prefisso IT e spazi tollerati)."""


class ImportPreviewAzienda(BaseModel):
    """Il minimo per rispondere a «è la mia azienda?». `stato_impresa` c'è per
    intercettare le cessate e le sospese PRIMA di importarle."""

    partita_iva: str
    ragione_sociale: str | None = None
    codice_fiscale: str | None = None
    forma_giuridica: str | None = None
    stato_impresa: str | None = None
    sede: str | None = None
    regione: str | None = None
    ateco: str | None = None
    legale_rappresentante: str | None = None
    numero_persone: int = 0


class ImportPreview(BaseModel):
    """Anteprima di SOLA LETTURA: nulla è stato scritto sui dati aziendali.

    `autofill` risponde all'altra domanda, «cosa verrà scritto?»: `applied`
    sono i campi vuoti che la conferma compilerà, `conflicts` quelli già
    valorizzati che differiscono e che NON verranno toccati. Sono calcolati
    con la stessa funzione che userà la conferma, quindi non possono mentire.

    `reused: true` = l'anteprima viene da un payload già pagato (nessun nuovo
    addebito). `draft_expires_at` è il momento oltre il quale va rifatta."""

    azienda: ImportPreviewAzienda
    autofill: AutofillOut
    suggestions: SuggestionsOut
    fetched_at: str
    draft_expires_at: str
    reused: bool = False
    sandbox: bool = False


class DossierResponse(BaseModel):
    editable: bool
    imported: bool
    fetched_at: str | None = None
    sandbox: bool | None = None
    dossier: dict | None = None
    people: list[PersonOut] = []
    derived: dict = {}
