import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

ClasseDimensionale = Literal["micro", "piccola", "media", "grande"]
FasciaFatturato = Literal["fino_100k", "100k_500k", "500k_2m", "2m_10m", "10m_50m", "oltre_50m"]


class CompanyIn(BaseModel):
    ragione_sociale: str = Field(min_length=1, max_length=300)
    forma_giuridica: str | None = Field(default=None, max_length=100)
    partita_iva: str
    codice_fiscale: str | None = Field(default=None, max_length=20)
    ateco_id: int | None = None
    settore_id: int | None = None
    regione_id: int | None = None
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


class CompanyResponse(BaseModel):
    editable: bool
    company: CompanyOut | None = None
