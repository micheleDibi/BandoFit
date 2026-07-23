from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.schemas.family import MeFamilyOut, PlanSwitchAdjustment
from app.schemas.job_position import JobPositionOut
from app.schemas.plan import PlanOut


class SubscriptionOut(BaseModel):
    id: UUID
    status: str
    data_inizio: date
    data_scadenza: date
    plan: PlanOut
    # True se l'abbonamento è quello del titolare della famiglia (figlio attivo).
    inherited: bool = False


class ProfileOut(BaseModel):
    id: UUID
    email: str
    nome: str | None = None
    cognome: str | None = None
    azienda: str | None = None
    telefono: str | None = None
    codice_fiscale: str | None = None
    cf_verified_at: datetime | None = None
    # Posizione anche come oggetto (embed): una voce disattivata resta
    # visibile a chi la aveva scelta (il catalogo è soft-disable).
    job_position_id: int | None = None
    job_position: JobPositionOut | None = None
    job_position_altro: str | None = None
    role: Literal["admin", "cliente", "progettista"]
    is_active: bool
    created_at: datetime


class ProgettistaOut(BaseModel):
    """Attributi del ruolo progettista (il codice è assegnato dal sistema)."""

    codice: str


class MeOut(BaseModel):
    profile: ProfileOut
    subscription: SubscriptionOut | None = None
    family: MeFamilyOut | None = None
    # Valorizzato per i progettisti e per gli admin che hanno già un codice
    # (assegnato alla prima proposta inviata — parità admin, 0019).
    progettista: ProgettistaOut | None = None
    # Limite EFFETTIVO di aziende gestibili (override utente > piano > 1;
    # dalla 0030 + addon companies). Per un membro attivo è il SUO (=1).
    max_aziende: int = 1
    # Flag child-aware per lo switcher (0031): per un membro ATTIVO è vero se
    # vede più di un'azienda (visibilità ∩ vive); per gli altri, max_aziende>1.
    multi_azienda: bool = False
    # Presente solo nella risposta di un cambio piano che ha causato retrocessioni.
    plan_switch_adjustment: PlanSwitchAdjustment | None = None


class ProfileUpdate(BaseModel):
    nome: str | None = Field(default=None, max_length=100)
    cognome: str | None = Field(default=None, max_length=100)
    azienda: str | None = Field(default=None, max_length=200)
    # Validato solo se presente nel payload: i valori legacy pre-0022 non in
    # E.164 restano intatti finché non vengono modificati (il client omette
    # la chiave quando il campo non cambia).
    telefono: str | None = Field(default=None, max_length=50)
    job_position_id: int | None = None
    job_position_altro: str | None = Field(default=None, max_length=100)
    # Salvabile anche senza verifica (il trigger DB azzera cf_verified_at se
    # cambia); la verifica all'Anagrafe è POST /me/verify-cf.
    codice_fiscale: str | None = Field(default=None, max_length=16)

    @field_validator("codice_fiscale")
    @classmethod
    def check_codice_fiscale(cls, value: str | None) -> str | None:
        from app.services.codice_fiscale import is_valid_cf, normalize_cf

        if value is None or value.strip() == "":
            return None
        cleaned = normalize_cf(value)
        if not is_valid_cf(cleaned):
            raise ValueError("Il codice fiscale non è formalmente valido")
        return cleaned

    @field_validator("telefono")
    @classmethod
    def check_telefono(cls, value: str | None) -> str | None:
        from app.services.telefono import is_valid_telefono, normalize_telefono

        if value is None or value.strip() == "":
            return None
        normalized = normalize_telefono(value)
        if not is_valid_telefono(normalized):
            raise ValueError("Il numero di telefono non è valido")
        return normalized

    @field_validator("job_position_altro")
    @classmethod
    def clean_job_position_altro(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class SwitchPlanIn(BaseModel):
    plan_id: int


class AdminSwitchPlanIn(BaseModel):
    plan_id: int
    # Obbligatoria: il cambio piano gratuito da admin va motivato nell'audit.
    motivazione: str = Field(min_length=1, max_length=500)


class VerifyCfIn(BaseModel):
    codice_fiscale: str = Field(min_length=16, max_length=16)


class VerifyCfOut(BaseModel):
    codice_fiscale: str
    cf_verified_at: datetime | None = None


class AdminFamilyInfo(BaseModel):
    type: Literal["parent", "child"]
    # figlio
    status: str | None = None
    parent_email: str | None = None
    # padre
    members_count: int | None = None


class AdminUserOut(BaseModel):
    profile: ProfileOut
    subscription: SubscriptionOut | None = None
    family: AdminFamilyInfo | None = None
    progettista: ProgettistaOut | None = None
    # Ragione sociale mostrata come «azienda» dell'utente: dal dossier
    # (company_profiles) del gruppo, con fallback al testo libero della
    # registrazione (profiles.azienda). Per i collegati attivi è quella del
    # titolare. Stessa priorità di family_service.parent_display_name.
    azienda_nome: str | None = None


class AdminUserUpdate(BaseModel):
    role: Literal["admin", "cliente", "progettista"] | None = None
    is_active: bool | None = None
    # Override del limite aziende: intero ≥1 per alzarlo/abbassarlo, `null`
    # esplicito per rimuoverlo (torna al default di piano). La chiave omessa
    # non tocca il valore (exclude_unset la distingue dal `null`).
    max_aziende_override: int | None = Field(default=None, ge=1)
