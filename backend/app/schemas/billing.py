"""Schemi dell'anagrafica di fatturazione (migration 0026, tipi 0029).

Il venditore è croato (ADVENTUS CONSULTING j.d.o.o., IVA 25%): i tipi di
soggetto sono due e il paese è QUALSIASI, per entrambi:
- azienda: ragione sociale + partita IVA (per l'Italia 11 cifre; per la UE
  forma VIES; extra-UE libera — identificativo fiscale locale);
- privato: nome e cognome; codice fiscale SOLO con paese IT.
CAP a 5 cifre e provincia obbligatoria SOLO per l'Italia.
I vincoli di forma stanno nello schema; i vincoli che richiedono I/O
(VIES) stanno in billing_service — e dalla 0029 NON bloccano il salvataggio:
decidono solo l'aliquota (senza prova VIES → 25%).
"""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

TipoSoggetto = Literal["azienda", "privato"]

# Paesi UE (ISO 3166-1 alpha-2), Italia inclusa: dal punto di vista del
# venditore croato l'Italia è un paese estero UE come gli altri. HR è nel set
# (è UE) ma il pricing esclude il paese del venditore dal reverse charge.
# GR usa il prefisso VIES 'EL', qui si registra il codice ISO del paese.
# Fonte unica: la importano billing_service (gate VIES) e pricing (aliquota).
PAESI_UE = frozenset({
    "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GR",
    "HR", "HU", "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO",
    "SE", "SI", "SK",
})

_RE_PIVA_IT = re.compile(r"^\d{11}$")
# Forma accettata dal VIES dopo il prefisso paese: 2-12 alfanumerici.
_RE_PIVA_VIES = re.compile(r"^[A-Z0-9]{2,12}$")
_RE_CF = re.compile(r"^[A-Z0-9]{16}$", re.IGNORECASE)
_RE_CAP_IT = re.compile(r"^\d{5}$")
_RE_PAESE = re.compile(r"^[A-Z]{2}$")


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

    @field_validator(
        "denominazione", "nome", "cognome", "partita_iva", "codice_fiscale",
        "provincia", "cap", "indirizzo", "comune",
        mode="before",
    )
    @classmethod
    def _trim(cls, v):
        if isinstance(v, str):
            v = v.strip()
        return v or None

    @field_validator("paese", "provincia", "codice_fiscale")
    @classmethod
    def _upper(cls, v):
        return v.strip().upper() if isinstance(v, str) else v

    def _normalizza_piva(self) -> None:
        """Porta la P.IVA nella forma che il VIES si aspetta DOPO il prefisso
        paese: maiuscola, senza spazi/punti, senza il prefisso stesso se
        l'utente l'ha digitato (per la Grecia il prefisso VIES è 'EL').
        Lo strip del prefisso vale SOLO per i paesi UE: per gli extra-UE il
        prefisso VIES non esiste e togliere le prime 2 lettere corromperebbe
        identificativi validi (es. lo svizzero 'CHE...' → 'E...')."""
        if not self.partita_iva:
            return
        piva = re.sub(r"[\s.]", "", self.partita_iva).upper()
        if self.paese in PAESI_UE:
            prefisso = "EL" if self.paese == "GR" else self.paese
            if piva.startswith(prefisso) and len(piva) > len(prefisso):
                piva = piva[len(prefisso):]
        self.partita_iva = piva or None

    @model_validator(mode="after")
    def _coerenza_per_tipo(self) -> "BillingProfileIn":
        errori: list[str] = []
        if not _RE_PAESE.fullmatch(self.paese):
            errori.append("il paese è un codice ISO di 2 lettere")
        # Regole italiane di forma: valgono solo per l'Italia.
        if self.paese == "IT":
            if not _RE_CAP_IT.fullmatch(self.cap):
                errori.append("il CAP italiano è di 5 cifre")
            if not self.provincia:
                errori.append("la provincia è obbligatoria per l'Italia")
        if self.tipo_soggetto == "azienda":
            if not self.denominazione:
                errori.append("la ragione sociale è obbligatoria")
            self._normalizza_piva()
            if self.paese == "IT":
                if not (self.partita_iva and _RE_PIVA_IT.fullmatch(self.partita_iva)):
                    errori.append("serve una partita IVA italiana di 11 cifre")
            elif self.paese in PAESI_UE:
                if not (self.partita_iva and _RE_PIVA_VIES.fullmatch(self.partita_iva)):
                    errori.append("serve la partita IVA del paese UE")
            else:  # extra-UE: identificativo fiscale locale, forma libera
                if not self.partita_iva or len(self.partita_iva) < 2:
                    errori.append("serve la partita IVA (o equivalente) del paese")
        else:  # privato
            if not (self.nome and self.cognome):
                errori.append("nome e cognome sono obbligatori")
            if self.paese == "IT":
                if not (self.codice_fiscale and _RE_CF.fullmatch(self.codice_fiscale)):
                    errori.append("serve un codice fiscale di 16 caratteri")
            # Estero: nessun identificativo fiscale richiesto per i privati.
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
