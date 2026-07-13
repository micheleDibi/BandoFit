from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.schemas.ai_check import AiCheckOut
from app.schemas.company import CompanyOut
from app.schemas.openapi_data import DossierResponse


class SlotIn(BaseModel):
    """Orari in UTC (timestamp ISO con offset): la conversione dal fuso
    dell'utente la fa il browser, il backend non assume alcun fuso."""

    inizio: datetime
    fine: datetime

    @field_validator("inizio", "fine")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Orario senza fuso: invia un timestamp ISO con offset (es. Z)")
        return value


class SlotOut(BaseModel):
    id: UUID
    inizio: datetime
    fine: datetime
    # Derivato: esiste una prenotazione confermata (nessuna colonna di stato a DB).
    prenotato: bool = False
    # Serie di ricorrenza (0018): null = slot singolo.
    serie_id: UUID | None = None


# Tetto per una serie di ricorrenza: giornaliera per 12 mesi = 367 occorrenze.
# Allineato al limite dentro fn_create_slot_serie (0018).
MAX_OCCORRENZE_SERIE = 370


class SerieIn(BaseModel):
    """Occorrenze già materializzate dal browser (l'unico a conoscere il fuso
    dell'utente: l'orario a muro resta stabile attraverso i cambi di ora
    legale); il backend valida ciascuna come uno slot singolo."""

    occorrenze: list[SlotIn] = Field(min_length=1, max_length=MAX_OCCORRENZE_SERIE)


class SerieCreateOut(BaseModel):
    serie_id: UUID
    creati: list[SlotOut]
    # Occorrenze scartate perché sovrapposte a slot esistenti (o tra loro).
    saltati: int


class SerieDeleteOut(BaseModel):
    eliminati: int
    # Slot prenotati: l'eliminazione della serie non li tocca mai.
    mantenuti: int


# ---------------------------------------------------------------------------
# Flusso richiesta → proposta → assegnazione → prenotazione
# ---------------------------------------------------------------------------


class CreateRequestIn(BaseModel):
    ai_check_id: UUID


class ProposalIn(BaseModel):
    messaggio: str = Field(min_length=1, max_length=4000)


class AcceptProposalIn(BaseModel):
    # Prenotazione contestuale opzionale: se lo slot è appena stato preso
    # fallisce TUTTA l'accettazione (all-or-nothing) e si riprova.
    slot_id: UUID | None = None


class BookIn(BaseModel):
    slot_id: UUID


class ProgettistaPublicOut(BaseModel):
    """Come il cliente vede il progettista assegnato: per NOME E COGNOME
    (più umano). Il codice resta nel payload per gli usi interni/admin,
    la UI del cliente non lo mostra."""

    codice: str | None = None
    nome: str | None = None


class ProposalOut(BaseModel):
    id: UUID
    codice_progettista: str | None = None
    # Il cliente vede l'autore della proposta per nome e cognome.
    nome_progettista: str | None = None
    messaggio: str
    stato: str
    created_at: datetime


class BookingOut(BaseModel):
    id: UUID
    inizio: datetime
    fine: datetime
    stato: str


class ConsulenzaOut(BaseModel):
    """Vista del cliente sulla propria richiesta di consulto."""

    id: UUID
    stato: str
    bando_id: int
    bando_slug: str
    bando_titolo: str
    esito: str | None = None
    punteggio: int | None = None
    created_at: datetime
    assigned_at: datetime | None = None
    # False per gli account collegati: vedono, non agiscono.
    editable: bool = False
    progettista: ProgettistaPublicOut | None = None
    proposte_aperte: int = 0
    # Valorizzate solo nel dettaglio.
    proposte: list[ProposalOut] = []
    appuntamento: BookingOut | None = None


class RichiestaPoolOut(BaseModel):
    """Vista PARZIALE del progettista (requisito punto 3): ragione sociale,
    P.IVA, denominazione utente, email, bando ed esito dell'AI-check.
    Tutto il resto arriva solo dopo l'assegnazione (dossier)."""

    id: UUID
    stato: str
    ragione_sociale: str | None = None
    partita_iva: str | None = None
    denominazione_utente: str
    email: str | None = None
    bando_id: int
    bando_slug: str
    bando_titolo: str
    esito: str | None = None
    punteggio: int | None = None
    created_at: datetime
    assegnata_a_me: bool = False
    mia_proposta_stato: str | None = None
    appuntamento: BookingOut | None = None


class RichiestaPoolDetailOut(RichiestaPoolOut):
    # Il report AI-check completo è visibile a TUTTI i progettisti sul pool:
    # imposto dal requisito (punto 3), citato nella nota GDPR dei docs.
    ai_check: AiCheckOut | None = None
    mie_proposte: list[ProposalOut] = []


class RichiestePoolResponse(BaseModel):
    aperte: list[RichiestaPoolOut]
    assegnate: list[RichiestaPoolOut]


class FullCompanyOut(BaseModel):
    """Vista FULL post-assegnazione: dati aziendali + dossier certificato.
    Ogni lettura è registrata in audit_log."""

    company: CompanyOut | None = None
    dossier: DossierResponse


class AppuntamentoOut(BaseModel):
    """Appuntamento visto dal progettista (post-assegnazione: dati cliente)."""

    id: UUID
    request_id: UUID
    inizio: datetime
    fine: datetime
    stato: str
    bando_titolo: str
    ragione_sociale: str | None = None
    email: str | None = None
