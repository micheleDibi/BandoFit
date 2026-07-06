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


class DossierResponse(BaseModel):
    editable: bool
    imported: bool
    fetched_at: str | None = None
    sandbox: bool | None = None
    dossier: dict | None = None
    people: list[PersonOut] = []
    derived: dict = {}


class DocumentOut(BaseModel):
    id: str
    kind: str
    endpoint: str
    status: str
    error_detail: str | None = None
    file_name: str | None = None
    file_size: int | None = None
    pages: int | None = None
    # Il testo estratto non viene mai spedito al client (serve all'AI-check):
    # qui solo il flag di disponibilità.
    has_text: bool = False
    cost_cents: int = 0
    sandbox: bool = False
    created_at: str
    ready_at: str | None = None


class DocumentsResponse(BaseModel):
    editable: bool
    documents: list[DocumentOut] = []
