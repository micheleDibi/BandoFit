import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

ClasseDimensionale = Literal["micro", "piccola", "media", "grande"]
FasciaFatturato = Literal["fino_100k", "100k_500k", "500k_2m", "2m_10m", "10m_50m", "oltre_50m"]


class BeneficiarioRef(BaseModel):
    """Copia denormalizzata di una riga della lookup `beneficiari` del DB
    secondario (nessuna FK cross-database), come settore_id/settore_nome."""

    id: int
    nome: str


class CompanyIn(BaseModel):
    ragione_sociale: str = Field(min_length=1, max_length=300)
    forma_giuridica: str | None = Field(default=None, max_length=100)
    partita_iva: str
    codice_fiscale: str | None = Field(default=None, max_length=20)
    ateco_id: int | None = None
    settore_id: int | None = None
    regione_id: int | None = None
    # Categorie di beneficiario dichiarate (lookup del catalogo). Multi-valore:
    # un'azienda può essere insieme PMI e Organismo di formazione.
    beneficiari_ids: list[int] = Field(default_factory=list, max_length=50)
    anno_fondazione: int | None = Field(default=None, ge=1800, le=2100)
    indirizzo: str | None = Field(default=None, max_length=300)
    comune: str | None = Field(default=None, max_length=100)
    provincia: str | None = Field(default=None, max_length=100)
    cap: str | None = None
    classe_dimensionale: ClasseDimensionale | None = None
    numero_dipendenti: int | None = Field(default=None, ge=0)
    fascia_fatturato: FasciaFatturato | None = None
    pec: str | None = Field(default=None, max_length=200)
    telefono: str | None = Field(default=None, max_length=50)
    sito_web: str | None = Field(default=None, max_length=300)

    @field_validator("beneficiari_ids")
    @classmethod
    def dedup_beneficiari(cls, value: list[int]) -> list[int]:
        return list(dict.fromkeys(value))

    @field_validator("partita_iva")
    @classmethod
    def check_partita_iva(cls, value: str) -> str:
        cleaned = value.strip().upper().removeprefix("IT").replace(" ", "")
        if not re.fullmatch(r"[0-9]{11}", cleaned):
            raise ValueError("La partita IVA deve essere composta da 11 cifre")
        return cleaned

    @field_validator("cap")
    @classmethod
    def check_cap(cls, value: str | None) -> str | None:
        if value is None or value.strip() == "":
            return None
        cleaned = value.strip()
        if not re.fullmatch(r"[0-9]{5}", cleaned):
            raise ValueError("Il CAP deve essere composto da 5 cifre")
        return cleaned

    @field_validator("pec")
    @classmethod
    def check_pec(cls, value: str | None) -> str | None:
        if value is None or value.strip() == "":
            return None
        cleaned = value.strip()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", cleaned):
            raise ValueError("La PEC non è un indirizzo email valido")
        return cleaned

    @field_validator("sito_web")
    @classmethod
    def check_sito_web(cls, value: str | None) -> str | None:
        if value is None or value.strip() == "":
            return None
        cleaned = value.strip()
        if not re.match(r"^(https?://)?[\w.-]+\.[a-zA-Z]{2,}", cleaned):
            raise ValueError("Il sito web non sembra un indirizzo valido")
        return cleaned


class CompanyOut(CompanyIn):
    ateco_codice: str | None = None
    ateco_descrizione: str | None = None
    settore_nome: str | None = None
    regione_nome: str | None = None
    # Come `settore_nome`: la copia col nome, che il client non deve inviare.
    beneficiari: list[BeneficiarioRef] = Field(default_factory=list)


class CompanyResponse(BaseModel):
    editable: bool
    company: CompanyOut | None = None


class CompanyCreate(BaseModel):
    """Creazione di una nuova azienda gestita (Advisor). Ragione sociale e
    P.IVA sono obbligatorie subito (colonne NOT NULL): niente "azienda vuota".
    L'import IT-full e il resto dei campi sono azioni successive."""

    ragione_sociale: str = Field(min_length=1, max_length=300)
    partita_iva: str

    @field_validator("partita_iva")
    @classmethod
    def check_partita_iva(cls, value: str) -> str:
        cleaned = value.strip().upper().removeprefix("IT").replace(" ", "")
        if not re.fullmatch(r"[0-9]{11}", cleaned):
            raise ValueError("La partita IVA deve essere composta da 11 cifre")
        return cleaned


class CompanySummary(BaseModel):
    """Voce dell'elenco delle aziende gestite (per lo switcher e la pagina
    Aziende). Non i dati completi: solo l'essenziale per identificarle."""

    id: UUID
    ragione_sociale: str
    partita_iva: str
    created_at: datetime
    # True per l'azienda che il resolver userebbe di default (la più vecchia
    # viva) — utile alla UI prima che l'utente scelga.
    attiva: bool = False


class CompaniesOut(BaseModel):
    aziende: list[CompanySummary] = Field(default_factory=list)
    # Limite effettivo (override utente > piano > 1) e quante ne sono in uso
    # (vive): la UI disabilita "crea" quando usate >= max.
    max_aziende: int = 1
    usate: int = 0


class CompanyFacetsOut(BaseModel):
    """Facet REALI dell'azienda, negli id delle lookup del catalogo.

    Non è un doppione di `CompanyOut`: là ci sono i campi del FORM (una sola
    regione, un solo ATECO), qui c'è tutto ciò che l'azienda è davvero secondo
    i dati certificati — `regioni` sono TUTTE le sedi (legale + unità locali) e
    `ateco` include le divisioni secondarie. Stessa fonte del badge di
    compatibilità e dell'AI-check, così i tre non possono divergere.

    `sufficiente` = P.IVA importata (ATECO e regione valorizzati): è la
    condizione del badge, non del preset «Bandi per te»."""

    regioni: list[int] = Field(default_factory=list)
    ateco: list[int] = Field(default_factory=list)
    settori: list[int] = Field(default_factory=list)
    beneficiari: list[int] = Field(default_factory=list)
    sufficiente: bool = False
