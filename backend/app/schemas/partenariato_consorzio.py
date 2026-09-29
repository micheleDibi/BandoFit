"""Contratti del consorzio della call (WP8, docs/partenariati.md §2.8 V1-V3).

Fonte UNICA dei DTO per backend e frontend. Tre famiglie:
  * input (`MembroAggiornaIn`, `MembroConfermaIn`, `EsternoIn`, `BudgetIn`,
    `DocumentoStatoIn`):
    `extra='forbid'`; le regole di dominio che l'utente può violare scrivendo
    (lunghezze, percentuali, budget fuori fascia) sollevano `BadRequestError`
    → 400 con un messaggio per l'utente, come le call del WP5; tipi sbagliati
    e codici fuori vocabolario restano la 422 generica;
  * proiezioni per destinatario (`ConsorzioOut` e le sue parti), a WHITELIST
    anche nella costruzione (`extra='forbid'`): mai `company_profile_id`,
    owner, utenti, valori esatti di bilancio di altri membri. Gli id dei
    membri (`partner_call_membri.id`) sono gli unici handle; le voci del
    validatore e la matrice vi rimandano;
  * `DocumentoConsorzioOut` (esportato anche come `DocumentoOut`, il nome del
    contratto): il nome della classe resta distinto da
    `partenariato_vocabolario.DocumentoOut` per non duplicare lo schema
    OpenAPI.

Ruoli dei membri: `capofila | partner | affiliated_entity |
associated_partner` (nella UI «Entità affiliata» e «Partner associato»);
stati `proposto ⇄ confermato → uscito → proposto`.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    create_model,
    field_validator,
    model_validator,
)

from app.core.errors import BadRequestError
from app.schemas.partenariato_criteri import AmbitoRequisito, EsitoCopertura, FonteCriterio
from app.schemas.partenariato_vocabolario import FormaAggregazione, TipoSoggetto
from app.schemas.partner_call import (
    BudgetFascia,
    CallAggiornaIn,
    CitazioneIn,
    paese_iso2,
    senza_invisibili,
)
from app.schemas.partner_profile import PartnerPubblicoOut

# ------------------------------------------------------------------ domini

RuoloMembro = Literal["capofila", "partner", "affiliated_entity", "associated_partner"]
StatoMembro = Literal["proposto", "confermato", "uscito"]
EsitoVoce = Literal["verde", "rosso", "grigio"]
# Chi ha stabilito la regola di una voce: «bando» = voce dello snapshot
# `regole_partenariato` confermata con la sua citazione (o dato del catalogo
# del bando), «creatore» = impostata o modificata dal creatore della call.
FonteRegola = Literal["bando", "creatore"]
CodiceVoce = Literal[
    "regole",  # snapshot delle regole assente
    "numero_partner",
    "composizione",
    "somma_quote",
    "quota_partner",
    "quota_categoria",
    "quota_capofila",
    "indipendenza",
    "paesi_distinti",
    "regola_finanziaria",
    "media_pesata",
    "esclusivita",
    "vincolo_membro",  # requisito della call valido per ogni membro
    "vincolo_da_verificare",  # regola del bando descritta solo a testo
    "membri_attivi",  # un'azienda membro non è più attiva in piattaforma
]
StatoDocumento = Literal["da_fare", "in_corso", "fatto", "non_applicabile"]
# Quando serve il documento: prima della domanda (accordi tra i partner), con
# la domanda, prima della concessione, entro la prima erogazione (se il bando
# lo chiede così).
FaseDocumento = Literal["accordo_preliminare", "domanda", "concessione", "prima_erogazione"]
FonteDocumento = Literal["base", "forma", "bando"]

# ------------------------------------------------------------------ limiti

MAX_MEMBRI_CALL = 30  # tetto di membri (anche esterni) per call, come la RPC
ESTERNO_NOME_MIN, ESTERNO_NOME_MAX = 2, 200
MAX_TIPI_ESTERNO = 5
MAX_NOTE_DOCUMENTO = 500
# Tolleranza della somma delle quote (V2): |somma − 100| ≤ 0,01.
TOLLERANZA_QUOTE = Decimal("0.01")

ETICHETTE_RUOLO_MEMBRO: dict[str, str] = {
    "capofila": "Capofila",
    "partner": "Partner",
    "affiliated_entity": "Entità affiliata",
    "associated_partner": "Partner associato",
}


# ----------------------------------------------------------------- utilità


def _quota(valore: Decimal | None) -> Decimal | None:
    """Quota % del membro: (0, 100], al più due decimali (numeric(5,2))."""
    if valore is None:
        return None
    if not valore.is_finite() or valore <= 0 or valore > 100:
        raise BadRequestError("La quota deve essere maggiore di 0 e al massimo 100")
    if valore.as_tuple().exponent < -2:
        raise BadRequestError("La quota può avere al massimo due decimali")
    return valore


# ----------------------------------------------------------------- input


class MembroAggiornaIn(BaseModel):
    """PUT /partenariati/call/{id}/consorzio/membri/{mid} (solo il creatore):
    posizione, ruolo e quota del membro, sostituiti tutti e tre (come i
    parametri di `fn_partner_membro_aggiorna`). Cambiare ruolo, posizione o
    quota di un membro diverso dal creatore lo riporta a `proposto`."""

    model_config = ConfigDict(extra="forbid")

    posizione_id: UUID | None = None
    ruolo: RuoloMembro
    quota_percentuale: Decimal | None = None

    @field_validator("quota_percentuale")
    @classmethod
    def _quota(cls, valore: Decimal | None) -> Decimal | None:
        return _quota(valore)


class MembroConfermaIn(BaseModel):
    """POST /partenariati/call/{id}/consorzio/membri/{mid}/conferma: ruolo,
    posizione e quota del membro COME LI HA VISTI chi conferma. La RPC li
    confronta con la riga bloccata: se nel frattempo il creatore li ha
    cambiati risponde 409 `membro_modificato` (una pagina non aggiornata non
    conferma termini che non ha mostrato)."""

    model_config = ConfigDict(extra="forbid")

    ruolo: RuoloMembro
    posizione_id: UUID | None = None
    quota_percentuale: Decimal | None = None

    @field_validator("quota_percentuale")
    @classmethod
    def _quota(cls, valore: Decimal | None) -> Decimal | None:
        return _quota(valore)


class EsternoIn(BaseModel):
    """POST …/consorzio/esterni e PUT …/consorzio/esterni/{mid} (solo il
    creatore, Q20): un membro che non è in piattaforma. Nome, paese e tipi di
    soggetto li DICHIARA il creatore: il validatore li usa marcandoli
    «dichiarato» e non ne verifica collegamenti né bilanci."""

    model_config = ConfigDict(extra="forbid")

    denominazione: str
    paese: str
    tipi_soggetto: list[TipoSoggetto] = Field(min_length=1)
    ruolo: RuoloMembro = "partner"
    posizione_id: UUID | None = None
    quota_percentuale: Decimal | None = None

    @field_validator("denominazione")
    @classmethod
    def _denominazione(cls, valore: str) -> str:
        pulito = " ".join(senza_invisibili(valore).split())
        if not ESTERNO_NOME_MIN <= len(pulito) <= ESTERNO_NOME_MAX:
            raise BadRequestError(
                f"Il nome del membro esterno deve avere tra {ESTERNO_NOME_MIN} e "
                f"{ESTERNO_NOME_MAX} caratteri"
            )
        return pulito

    @field_validator("paese")
    @classmethod
    def _paese(cls, valore: str) -> str:
        return paese_iso2(valore)

    @field_validator("tipi_soggetto")
    @classmethod
    def _tipi(cls, valori: list[str]) -> list[str]:
        unici = list(dict.fromkeys(valori))
        if len(unici) > MAX_TIPI_ESTERNO:
            raise BadRequestError(
                f"Puoi indicare al massimo {MAX_TIPI_ESTERNO} tipi di soggetto"
            )
        return unici

    @field_validator("quota_percentuale")
    @classmethod
    def _quota(cls, valore: Decimal | None) -> Decimal | None:
        return _quota(valore)

    def payload(self, membro_id: UUID | str | None = None) -> dict[str, Any]:
        """`p_payload` di `fn_partner_membro_esterno` (0040: chiavi `membro_id?,
        denominazione, paese, tipi_soggetto, ruolo, quota, posizione_id`, nient'altro);
        `membro_id` solo per modificare un esterno."""
        dati: dict[str, Any] = {
            "denominazione": self.denominazione,
            "paese": self.paese,
            "tipi_soggetto": list(self.tipi_soggetto),
            "ruolo": self.ruolo,
            "quota": str(self.quota_percentuale) if self.quota_percentuale is not None else None,
            "posizione_id": str(self.posizione_id) if self.posizione_id else None,
        }
        if membro_id is not None:
            dati["membro_id"] = str(membro_id)
        return dati


class BudgetIn(BaseModel):
    """PUT …/consorzio/budget (solo il creatore): fascia pubblica e budget
    esatto RISERVATO (C4), con le stesse regole dell'aggiornamento della call
    (importo positivo, due decimali, dentro la fascia)."""

    model_config = ConfigDict(extra="forbid")

    budget_fascia: BudgetFascia
    budget_progetto_eur: Decimal | None = None

    @model_validator(mode="after")
    def _come_la_call(self):
        # Stesse verifiche (e messaggi) di `CallAggiornaIn`: una sola regola.
        CallAggiornaIn(
            budget_fascia=self.budget_fascia, budget_progetto_eur=self.budget_progetto_eur
        )
        return self

    def campi(self) -> dict[str, Any]:
        """I campi nella forma di `p_campi` dell'aggiornamento della call."""
        return self.model_dump(mode="json")


class DocumentoStatoIn(BaseModel):
    """PUT …/consorzio/documenti/{codice} (solo il creatore)."""

    model_config = ConfigDict(extra="forbid")

    stato: StatoDocumento
    note: str | None = None

    @field_validator("note")
    @classmethod
    def _note(cls, valore: str | None) -> str | None:
        if valore is None:
            return None
        pulito = senza_invisibili(valore).strip()
        if not pulito:
            return None
        if len(pulito) > MAX_NOTE_DOCUMENTO:
            raise BadRequestError(
                f"Le note possono avere al massimo {MAX_NOTE_DOCUMENTO} caratteri"
            )
        return pulito


# ------------------------------------------------------------------ output


class _Uscita(BaseModel):
    # Whitelist anche nella costruzione, come le proiezioni del WP5-WP7.
    model_config = ConfigDict(extra="forbid")


# Il profilo pubblico del WP4 (Q12 per gli anonimi) SENZA `codice_pubblico`,
# come `partenariato_accesso.ProfiloSuggeritoOut`: stabile tra le call,
# permetterebbe di riconoscere lo stesso anonimo su call diverse.
ProfiloMembroOut = create_model(
    "ProfiloMembroOut",
    __base__=_Uscita,
    **{
        nome: (campo.annotation, campo)
        for nome, campo in PartnerPubblicoOut.model_fields.items()
        if nome != "codice_pubblico"
    },
)


class PosizioneMembroOut(_Uscita):
    id: UUID
    titolo: str


class MembroOut(_Uscita):
    """Un membro come lo vede il destinatario (Q11, T3).

    - `nome`: per gli esterni la denominazione dichiarata dal creatore; per i
      membri in piattaforma lo decide il servizio: la propria azienda col suo
      nome, gli altri con lo pseudonimo della call (rivelazione spenta, WP7)
      e i soli dati anonimi di `profilo`;
    - `tipi_soggetto`: solo per gli esterni (dichiarati dal creatore);
    - mai `company_profile_id`, owner, contatti o importi di bilancio."""

    id: UUID
    esterno: bool = False
    creatore: bool = False
    sei_tu: bool = False
    nome: str
    pseudonimo: str | None = None
    profilo: ProfiloMembroOut | None = None  # type: ignore[valid-type]
    paese: str | None = None
    tipi_soggetto: list[TipoSoggetto] = Field(default_factory=list)
    ruolo: RuoloMembro
    posizione: PosizioneMembroOut | None = None
    quota_percentuale: Decimal | None = None
    stato: StatoMembro
    confermato_at: datetime | None = None
    puo_modificare: bool = False
    puo_confermare: bool = False
    puo_uscire: bool = False


class RegolaOrigineOut(_Uscita):
    """Da dove viene la regola di una voce: `citazione` solo per le voci del
    bando confermate con il passaggio ritrovato su una pagina di un documento
    ufficiale."""

    fonte: FonteRegola
    citazione: CitazioneIn | None = None


class EsitoMembroOut(_Uscita):
    """Esito di una voce su UN membro: in vista «terzi» (fasce) per gli altri,
    in vista «proprio» per il destinatario stesso."""

    membro_id: UUID
    esito: EsitoVoce
    dichiarato: bool = False


class VoceOut(_Uscita):
    """Una voce della checklist del validatore (V2). `dettaglio_pubblico` non
    contiene mai nomi, id o valori di bilancio; `dettaglio_privato` esiste
    solo per il destinatario (i suoi dati, anche esatti). `membri_coinvolti`
    = i membri per cui la voce non è verde (o che la determinano, per la
    composizione); per indipendenza ed esclusività un membro che non ha
    creato la call vede solo sé stesso. `dichiarato` = l'esito si regge su dati
    dichiarati (profilo partner o membri esterni)."""

    id: str
    codice: CodiceVoce
    esito: EsitoVoce
    titolo: str
    dettaglio_pubblico: str
    dettaglio_privato: str | None = None
    regola: RegolaOrigineOut | None = None
    membri_coinvolti: list[UUID] = Field(default_factory=list)
    esiti_membri: list[EsitoMembroOut] = Field(default_factory=list)
    dichiarato: bool = False


class RiepilogoValidazioneOut(_Uscita):
    verde: int = 0
    rosso: int = 0
    grigio: int = 0


class ValidazioneOut(_Uscita):
    """Esito complessivo (rosso se c'è un rosso, altrimenti grigio se c'è un
    grigio, altrimenti verde) e voci, come le vede il destinatario."""

    esito: EsitoVoce
    voci: list[VoceOut] = Field(default_factory=list)
    riepilogo: RiepilogoValidazioneOut = Field(default_factory=RiepilogoValidazioneOut)


class CellaMatriceOut(_Uscita):
    """Copertura di un requisito da parte di un membro (`valuta_criterio`):
    vista «proprio» per la colonna del destinatario, «terzi» per le altre.
    `testo_privato` solo nella propria colonna. `si_applica` falso per i
    requisiti «di ogni membro» sui partner associati."""

    membro_id: UUID
    esito: EsitoCopertura
    fonte: FonteCriterio | None = None
    testo: str
    testo_privato: str | None = None
    si_applica: bool = True


class RigaMatriceOut(_Uscita):
    requisito_id: UUID
    etichetta: str
    testo: str | None = None
    ambito: AmbitoRequisito
    cercato: bool = False
    # consorzio: verde se un membro lo copre; ogni_membro: verde se lo coprono
    # tutti i membri a cui si applica.
    esito: EsitoVoce
    celle: list[CellaMatriceOut] = Field(default_factory=list)


class MatriceOut(_Uscita):
    """Matrice requisiti × membri (V3). `copertura_gap_ratio` = requisiti
    cercati coperti da almeno un membro / cercati (null senza cercati)."""

    membri: list[UUID] = Field(default_factory=list)
    righe: list[RigaMatriceOut] = Field(default_factory=list)
    copertura_gap_ratio: float | None = None


class DocumentoConsorzioOut(_Uscita):
    """Un documento della checklist per forma (appendice A) con il suo stato.
    Lo stato lo cambia solo il creatore."""

    codice: str
    titolo: str
    fase: FaseDocumento
    obbligatorio: bool
    nota: str | None = None
    fonte: FonteDocumento = "base"
    stato: StatoDocumento = "da_fare"
    note: str | None = None
    updated_at: datetime | None = None


# Nome del contratto WP8.
DocumentoOut = DocumentoConsorzioOut


class BudgetOut(_Uscita):
    """Fascia pubblica e budget esatto RISERVATO (C4): `esatto` solo per chi
    ne ha diritto (creatore e controparti accettate, come la vista della call
    del WP7), altrimenti null."""

    fascia: BudgetFascia | None = None
    esatto: Decimal | None = None
    modificabile: bool = False


class ConsorzioOut(_Uscita):
    """GET /partenariati/call/{id}/consorzio (creatore e controparti).
    `modificabile`: chi guarda è il titolare dell'azienda creatrice e la call
    è in uno stato in cui il consorzio si modifica (pubblicata, scaduta,
    chiusa come completata): membri esterni e documenti. Per un'azienda uscita
    dal consorzio la risposta ha solo la sua riga (nessun altro membro,
    voce, requisito, documento né budget esatto)."""

    membri: list[MembroOut] = Field(default_factory=list)
    validazione: ValidazioneOut
    matrice: MatriceOut = Field(default_factory=MatriceOut)
    documenti: list[DocumentoConsorzioOut] = Field(default_factory=list)
    budget: BudgetOut = Field(default_factory=BudgetOut)
    forma: FormaAggregazione | None = None
    editable: bool = False
    sei_creatore: bool = False
    modificabile: bool = False
    validazione_at: datetime | None = None
    membri_max: int = MAX_MEMBRI_CALL
