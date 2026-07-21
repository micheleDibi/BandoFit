"""Schemi dell'anagrafica di fatturazione (migration 0026).

La validazione DIPENDE dal tipo di soggetto (chi compra):
- azienda_it: P.IVA italiana (11 cifre) + recapito SDI (codice destinatario a
  7 caratteri, oppure PEC con destinatario '0000000');
- privato_it: codice fiscale (16 caratteri) — fattura B2C con '0000000';
- azienda_ue: paese UE ≠ IT + P.IVA locale; il VIES si verifica nel servizio
  (chiamata esterna), non qui.
I vincoli di forma stanno nello schema; i vincoli che richiedono I/O
(VIES, esistenza) stanno in billing_service.
"""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

TipoSoggetto = Literal["azienda_it", "privato_it", "azienda_ue"]

# Paesi UE (ISO 3166-1 alpha-2) ammessi per azienda_ue. Niente IT (usa
# azienda_it) e niente extra-UE (fuori scope v1). GR usa il prefisso VIES
# 'EL' ma qui si registra il codice ISO del paese.
_PAESI_UE = {
    "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GR",
    "HR", "HU", "IE", "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO", "SE",
    "SI", "SK",
}

_RE_PIVA_IT = re.compile(r"^\d{11}$")
_RE_CF = re.compile(r"^[A-Z0-9]{16}$", re.IGNORECASE)
_RE_CAP_IT = re.compile(r"^\d{5}$")
_RE_SDI = re.compile(r"^[A-Z0-9]{7}$", re.IGNORECASE)


class BillingProfileIn(BaseModel):
    tipo_soggetto: TipoSoggetto
    denominazione: str | None = Field(default=None, max_length=200)
    nome: str | None = Field(default=None, max_length=100)
    cognome: str | None = Field(default=None, max_length=100)
    partita_iva: str | None = Field(default=None, max_length=20)
    codice_fiscale: str | None = Field(default=None, max_length=16)
    paese: str = Field(default="IT", min_length=2, max_length=2)
    indirizzo: str = Field(min_length=1, max_length=200)
    comune: str = Field(min_length=1, max_length=100)
    provincia: str | None = Field(default=None, max_length=2)
    cap: str = Field(min_length=1, max_length=10)
    codice_destinatario: str | None = Field(default=None, max_length=7)
    pec: str | None = Field(default=None, max_length=200)

    @field_validator(
        "denominazione", "nome", "cognome", "partita_iva", "codice_fiscale",
        "provincia", "cap", "codice_destinatario", "pec", "indirizzo", "comune",
        mode="before",
    )
    @classmethod
    def _trim(cls, v):
        if isinstance(v, str):
            v = v.strip()
        return v or None

    @field_validator("paese", "provincia", "codice_fiscale", "codice_destinatario")
    @classmethod
    def _upper(cls, v):
        return v.upper() if isinstance(v, str) else v

    @model_validator(mode="after")
    def _coerenza_per_tipo(self) -> "BillingProfileIn":
        errori: list[str] = []
        if self.tipo_soggetto == "azienda_it":
            if self.paese != "IT":
                errori.append("un'azienda italiana ha paese IT")
            if not self.denominazione:
                errori.append("la ragione sociale è obbligatoria")
            if not (self.partita_iva and _RE_PIVA_IT.fullmatch(self.partita_iva)):
                errori.append("serve una partita IVA italiana di 11 cifre")
            if not _RE_CAP_IT.fullmatch(self.cap):
                errori.append("il CAP italiano è di 5 cifre")
            if not self.provincia:
                errori.append("la provincia è obbligatoria")
            # Recapito SDI: codice destinatario O PEC (con destinatario 0000000)
            if self.codice_destinatario and not _RE_SDI.fullmatch(self.codice_destinatario):
                errori.append("il codice destinatario SDI è di 7 caratteri")
            if not self.codice_destinatario and not self.pec:
                errori.append("serve il codice destinatario SDI oppure la PEC")
        elif self.tipo_soggetto == "privato_it":
            if self.paese != "IT":
                errori.append("un privato italiano ha paese IT")
            if not (self.nome and self.cognome):
                errori.append("nome e cognome sono obbligatori")
            if not (self.codice_fiscale and _RE_CF.fullmatch(self.codice_fiscale)):
                errori.append("serve un codice fiscale di 16 caratteri")
            if not _RE_CAP_IT.fullmatch(self.cap):
                errori.append("il CAP italiano è di 5 cifre")
            # B2C: la fattura viaggia sempre con destinatario '0000000'
            self.codice_destinatario = None
        else:  # azienda_ue
            if self.paese not in _PAESI_UE:
                errori.append("il paese deve essere UE (diverso dall'Italia)")
            if not self.denominazione:
                errori.append("la ragione sociale è obbligatoria")
            if not self.partita_iva or len(self.partita_iva) < 4:
                errori.append("serve la partita IVA del paese UE")
            # Nessun recapito SDI per l'estero: il campo non si applica.
            self.codice_destinatario = None
        if errori:
            raise ValueError("; ".join(errori))
        return self


class BillingProfileOut(BaseModel):
    tipo_soggetto: TipoSoggetto
    denominazione: str | None = None
    nome: str | None = None
    cognome: str | None = None
    partita_iva: str | None = None
    codice_fiscale: str | None = None
    paese: str
    indirizzo: str
    comune: str
    provincia: str | None = None
    cap: str
    codice_destinatario: str | None = None
    pec: str | None = None
    vies_valid: bool | None = None
    vies_checked_at: str | None = None
    completo: bool = True


class BillingPrefillOut(BaseModel):
    """Precompilazione proposta (dai dati dell'azienda): MAI persistita da sola."""

    tipo_soggetto: TipoSoggetto | None = None
    denominazione: str | None = None
    partita_iva: str | None = None
    codice_fiscale: str | None = None
    indirizzo: str | None = None
    comune: str | None = None
    provincia: str | None = None
    cap: str | None = None
    pec: str | None = None
