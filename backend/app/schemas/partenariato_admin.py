"""Admin del modulo partenariati (WP9, W3 e verifica dell'identità da parte
dell'admin decisa da Michele): elenco delle call, metriche, costi per valuta,
coda della verifica d'identità.

Le metriche e i costi sono le forme restituite da
`fn_admin_metriche_partenariati` e `fn_admin_costi_partenariati` (0041):
aggregati in SQL, valori non calcolabili NULL, costi in centesimi della
valuta della voce (EUR openapi, USD Anthropic) e MAI sommati tra valute."""

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from app.core.errors import AppError

StatoIdentita = Literal["non_richiesta", "richiesta", "verificata", "rifiutata"]
MetodoIdentita = Literal["telefonata_sede", "documento_legale_rappresentante", "pec", "altro"]
NOTA_MAX = 500


class BandoCallAdminOut(BaseModel):
    id: int | None = None
    slug: str | None = None
    titolo: str | None = None


class CreatoreCallAdminOut(BaseModel):
    company_profile_id: UUID
    ragione_sociale: str | None = None


class CallAdminOut(BaseModel):
    """Una call nell'elenco dell'admin (tutte le aziende)."""

    id: UUID
    titolo: str | None = None
    stato: str
    stato_prima_sospensione: str | None = None
    visibilita: str | None = None
    anonima: bool | None = None
    bando: BandoCallAdminOut
    creatore: CreatoreCallAdminOut
    pubblicata_at: datetime | None = None
    scadenza_call: date | None = None
    created_at: datetime | None = None
    sospesa_at: datetime | None = None
    sospeso_motivo: str | None = None
    validazione_esito: Literal["verde", "rosso", "grigio"] | None = None
    # Candidature spontanee e inviti (in qualunque stato), membri del
    # consorzio non usciti, segnalazioni della call ancora aperte.
    candidature: int = 0
    inviti: int = 0
    membri: int = 0
    segnalazioni_aperte: int = 0


class TassoAccettazioneOut(BaseModel):
    accettate: int = 0
    rifiutate: int = 0
    tasso: float | None = None


class MetricheOut(BaseModel):
    """Metriche sulle call PUBBLICATE nel periodo (giorni Europe/Rome,
    estremi compresi). `percentuale_call_con_candidatura_30_giorni` si
    calcola solo sulle call pubblicate da almeno 30 giorni (osservabili);
    le mediane e la prima candidatura contano solo le candidature spontanee.
    `consorzi_validati_verde` = ultima validazione salvata verde (aggiornata
    anche dal passo `ricalcolo_validazioni` dello scheduler)."""

    da: date
    a: date
    call_pubblicate: int = 0
    candidature: int = 0
    inviti: int = 0
    candidature_per_call: float | None = None
    call_osservabili_30_giorni: int = 0
    call_con_candidatura_30_giorni: int = 0
    percentuale_call_con_candidatura_30_giorni: float | None = None
    accettazione: dict[Literal["candidatura", "invito"], TassoAccettazioneOut] | None = None
    ore_mediane_prima_candidatura: float | None = None
    copertura_media_gap: float | None = None
    consorzi_validati: int = 0
    consorzi_validati_verde: int = 0


class VoceCostoOut(BaseModel):
    provider: str
    service: str
    outcome: str
    valuta: Literal["EUR", "USD"]
    eventi: int
    cost_cents: int


class TotaleCostoOut(BaseModel):
    valuta: Literal["EUR", "USD"]
    eventi: int
    cost_cents: int


class CostiOut(BaseModel):
    """Costi del modulo per provider, servizio, esito e VALUTA; i totali sono
    per valuta (mai un totale unico)."""

    da: date
    a: date
    voci: list[VoceCostoOut]
    totali: list[TotaleCostoOut]


class RegistroIdentitaOut(BaseModel):
    """Dati del Registro Imprese (import openapi) che servono all'admin per
    verificare: recapiti della SEDE dal registro, mai quelli inseriti
    dall'utente."""

    denominazione: str | None = None
    partita_iva: str | None = None
    stato_impresa: str | None = None
    comune: str | None = None
    provincia: str | None = None
    pec: str | None = None
    telefono: str | None = None


class TitolareIdentitaOut(BaseModel):
    nome: str | None = None
    email: str | None = None


class IdentitaAdminOut(BaseModel):
    """Una riga della coda della verifica dell'identità."""

    company_profile_id: UUID
    ragione_sociale: str | None = None
    denominazione_registro: str | None = None
    partita_iva: str | None = None
    stato: StatoIdentita
    metodo: MetodoIdentita | None = None
    richiesta_at: datetime | None = None
    verificata_at: datetime | None = None
    aggiornato_at: datetime | None = None
    # Nota dell'ultima richiesta del titolare (come preferisce essere contattato).
    nota: str | None = None
    titolare: TitolareIdentitaOut | None = None
    # Stessa regola di `fn_partenariato_identita_ok` (T5): `registro_ok` con
    # i dati del registro coerenti; altrimenti `registro_motivo` =
    # dati_non_importati | piva_diversa | impresa_non_attiva | dati_sandbox, e
    # non si verifica.
    registro_ok: bool
    registro_motivo: str | None = None
    registro: RegistroIdentitaOut


class IdentitaEsitoOut(BaseModel):
    """Esito di una decisione o di una revoca dell'admin."""

    company_profile_id: UUID
    stato: StatoIdentita
    metodo: MetodoIdentita | None = None
    verificata_at: datetime | None = None
    modificato: bool


def _nota(valore: Any, code: str, messaggio: str, *, obbligatoria: bool) -> str | None:
    testo = valore.strip() if isinstance(valore, str) else None
    if valore is not None and not isinstance(valore, str):
        raise AppError(400, code, messaggio)
    if not testo:
        if obbligatoria:
            raise AppError(400, code, messaggio)
        return None
    if len(testo) > NOTA_MAX:
        raise AppError(400, code, messaggio)
    return testo


class IdentitaDecisioneIn(BaseModel):
    """POST /admin/partenariati/identita/{company_id}/decidi: metodo
    obbligatorio con `verificata` (ignorato con `rifiutata`); nota ≤ 500,
    MAI dati personali oltre il necessario."""

    model_config = ConfigDict(extra="forbid")

    esito: Literal["verificata", "rifiutata"]
    metodo: MetodoIdentita | None = None
    nota: str | None = None

    @field_validator("nota", mode="before")
    @classmethod
    def _nota(cls, valore: Any) -> str | None:
        return _nota(valore, "bad_request", "La nota può avere al massimo 500 caratteri",
                     obbligatoria=False)


class IdentitaRevocaIn(BaseModel):
    """POST /admin/partenariati/identita/{company_id}/revoca."""

    model_config = ConfigDict(extra="forbid")

    motivo: str

    @field_validator("motivo", mode="before")
    @classmethod
    def _motivo(cls, valore: Any) -> str:
        return _nota(valore, "motivo_obbligatorio",
                     "Indica il motivo della revoca (al massimo 500 caratteri)",
                     obbligatoria=True)
