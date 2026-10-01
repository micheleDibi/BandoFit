"""Contratti delle call di partenariato (WP5, docs/partenariati.md §2.5).

Fonte UNICA dei DTO per backend e frontend. Tre famiglie:
  * input (`CallCreaIn`, `CallAggiornaIn`, `RegoleConfermaIn`, `RequisitiIn`,
    `PosizioniIn`, `ChiudiIn`, `SegnalazioneIn`): `extra='forbid'`. Le regole
    di DOMINIO che l'utente può violare scrivendo (lunghezze, cardinalità
    della DDL 0037, importi, coerenze) sollevano `BadRequestError` → 400
    `bad_request` con un messaggio per l'utente, come il profilo partner;
    tipi sbagliati, codici fuori vocabolario e campi sconosciuti restano la
    422 `validation_error` generica (li compone l'editor tipizzato).
  * snapshot delle regole confermate dal creatore (`RegoleCallSnapshot`,
    colonna `partner_calls.regole_partenariato`): matching (WP6) e validatore
    (WP8) leggono SOLO questo, mai l'estrazione grezza.
  * DTO in uscita: vista del creatore (`CallVistaCreatoreOut`), proiezioni a
    WHITELIST verso terzi (`CallPubblicaOut`, `CallCardOut`, `extra='forbid'`
    anche in costruzione), anteprima, versioni, segnalazioni.

Call nominative (WP9, decisione di Michele): `anonima: false` solo per le
aziende con l'identità verificata dalla piattaforma (altrimenti 409
`identita_non_verificata_admin` dal servizio; 409 `nominativo_non_disponibile`
con l'interruttore globale `NOMINATIVO_DISPONIBILE` spento). Verso terzi il
creatore di una call nominativa compare con la sola denominazione del
Registro Imprese (`CreatoreCallOut.anonima` false) finché l'identità resta
verificata: con la verifica revocata torna «Azienda anonima».
"""

import math
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    ValidationInfo,
    field_validator,
    model_validator,
)

from app.core.errors import BadRequestError
from app.schemas.entitlement import PartenariatiEntitlement
from app.schemas.partenariato import (
    AmbitoQuota,
    BaseCalcolo,
    Costituzione,
    EffettoViolazione,
    Modalita,
    Momento,
    RuoloComposizione,
    StatoPartenariato,
    TipoDocumentoRichiesto,
    TipoVincolo,
)
from app.schemas.partenariato_criteri import (
    AmbitoRequisito,
    CriterioPartner,
    CriterioRegolaFinanziaria,
    Dimensione,
    EsitoCopertura,
)
from app.schemas.partenariato_vocabolario import (
    Competenza,
    FormaAggregazione,
    RuoloPartenariato,
    TipoSoggetto,
)
from app.schemas.partner_profile import PAESI_ISO2, AtecoSezioneOut
from app.schemas.regole_finanziarie import RegolaFinanziaria
from app.services.link_policy import url_documento_pubblicabile
# Forma canonica e testi senza caratteri invisibili: definiti con i controlli
# anti-contatti (stessa difesa per profilo, call e messaggi), riesportati qui.
from app.services.partenariato_anonimato import forma_canonica, senza_invisibili  # noqa: F401

# ------------------------------------------------------------------ domini

RuoloCreatore = Literal["capofila", "cerco_capofila"]
# Le 7 forme della DDL: «altra» serve solo alle regole estratte dai bandi.
FormaPrevista = Literal[
    "ats",
    "ati_rti",
    "rete_contratto",
    "rete_soggetto",
    "consorzio",
    "accordo_partenariato",
    "consorzio_ue",
]
StatoCall = Literal[
    "bozza",
    "pubblicata",
    "chiusa_completata",
    "chiusa_annullata",
    "scaduta",
    "sospesa_moderazione",
]
MotivoChiusura = Literal[
    "scadenza_call",
    "bando_chiuso",
    "bando_sospeso",
    "bando_revocato",
    "bando_non_disponibile",
    "azienda_non_disponibile",
    "creatore_completata",
    "creatore_annullata",
    "moderazione",
]
Visibilita = Literal["pubblica", "solo_invitati"]
BudgetFascia = Literal[
    "fino_50k",
    "50k_150k",
    "150k_300k",
    "300k_500k",
    "500k_1m",
    "1m_2m",
    "2m_5m",
    "oltre_5m",
]
# Estremi in euro (minimo escluso, massimo incluso; None = senza tetto): la
# fascia pubblica del budget di progetto (C4).
ESTREMI_BUDGET: dict[str, tuple[int, int | None]] = {
    "fino_50k": (0, 50_000),
    "50k_150k": (50_000, 150_000),
    "150k_300k": (150_000, 300_000),
    "300k_500k": (300_000, 500_000),
    "500k_1m": (500_000, 1_000_000),
    "1m_2m": (1_000_000, 2_000_000),
    "2m_5m": (2_000_000, 5_000_000),
    "oltre_5m": (5_000_000, None),
}
OrigineRequisito = Literal[
    "ai_check", "precheck", "bando_partenariato", "regola_finanziaria", "manuale"
]
FonteCopertura = Literal["registro", "bilanci", "dichiarato", "ai_check", "nessuna"]
TerritorioModalita = Literal["qualsiasi", "sede_attuale", "sede_entro_erogazione"]
StatoJobAi = Literal["nessuno", "in_corso", "pronta", "errore"]
OrigineVoce = Literal["confermata", "modificata", "aggiunta"]
OggettoSegnalazione = Literal["call", "profilo", "messaggio"]
MotivoSegnalazione = Literal[
    "contenuto_illecito",
    "dati_personali",
    "spam_pubblicita",
    "contatti_nel_testo",
    "discriminatorio",
    "impersonificazione",
    "altro",
]
StatoSegnalazione = Literal[
    "ricevuta", "in_esame", "decisa", "ricorso_presentato", "ricorso_deciso"
]

# ------------------------------------------------------------------ limiti

# Lunghezze e cardinalità: le stesse CHECK della DDL 0037.
TITOLO_MIN, TITOLO_MAX = 10, 140
MAX_DESCRIZIONE_PUBBLICA = 3000
MAX_DETTAGLI_RISERVATI = 5000
MAX_PROFILO_IDEALE = 2000
OVERRIDE_MIN, OVERRIDE_MAX = 20, 1000
MAX_BUDGET_EUR = Decimal("999999999999.99")  # numeric(14,2)
ETICHETTA_MAX = 60
TESTO_REQUISITO_MIN, TESTO_REQUISITO_MAX = 3, 500
RIF_ORIGINE_MAX = 40
TITOLO_POSIZIONE_MIN, TITOLO_POSIZIONE_MAX = 3, 120
MAX_NOTE_POSIZIONE = 500
MAX_TIPI_POSIZIONE = 5
MAX_COMPETENZE_POSIZIONE = 10
MAX_DIVISIONI_POSIZIONE = 10
MAX_REGIONI_POSIZIONE = 21
MAX_PAESI_POSIZIONE = 30
MAX_REQUISITI_POSIZIONE = 20
NUMERO_MAX_POSIZIONE = 10
SEGNALAZIONE_MIN, SEGNALAZIONE_MAX = 10, 2000
# Senza CHECK a DB: tetti prudenti del replace-all.
MAX_REQUISITI = 40
MAX_POSIZIONI = 10
# Testi della citazione e delle voci dello snapshot (come la post-elaborazione WP3).
MAX_TESTO_VOCE = 2000
MAX_TESTO_BREVE = 500
MAX_VOCI_SNAPSHOT = 30
ID_VOCE_MAX = 20

_ALIAS_PAESI = {"EL": "GR", "UK": "GB"}


# ----------------------------------------------------------------- utilità


def _cifre(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _testo(
    valore: str | None, cosa: str, *, massimo: int, minimo: int = 0, vuoto_none: bool = True
) -> str | None:
    """Testo inserito dall'utente: senza caratteri invisibili
    (`senza_invisibili`), spazi ai bordi tolti, vuoto → None, lunghezze con
    messaggio."""
    if valore is None:
        return None
    pulito = senza_invisibili(valore).strip()
    if not pulito and vuoto_none:
        return None
    if len(pulito) < minimo:
        raise BadRequestError(f"{cosa} deve avere almeno {minimo} caratteri")
    if len(pulito) > massimo:
        raise BadRequestError(f"{cosa} può avere al massimo {_cifre(massimo)} caratteri")
    return pulito


def _al_massimo(valori: list, massimo: int, cosa: str) -> list:
    if len(valori) > massimo:
        raise BadRequestError(f"Puoi indicare al massimo {massimo} {cosa}")
    return valori


def _dedup(valori: list) -> list:
    return list(dict.fromkeys(valori))


def _percentuale(valore: Decimal | None, cosa: str) -> Decimal | None:
    if valore is None:
        return None
    if not valore.is_finite() or valore <= 0 or valore > 100:
        raise BadRequestError(f"{cosa} deve essere maggiore di 0 e al massimo 100")
    if valore.as_tuple().exponent < -2:
        raise BadRequestError(f"{cosa} può avere al massimo due decimali")
    return valore


def paese_iso2(valore: str) -> str:
    codice = valore.strip().upper()
    codice = _ALIAS_PAESI.get(codice, codice)
    if codice not in PAESI_ISO2:
        raise BadRequestError(f"Codice paese non valido: «{valore[:10]}»")
    return codice


def budget_nella_fascia(fascia: str, importo: Decimal) -> bool:
    """Il budget esatto rientra nella fascia pubblica (minimo escluso,
    massimo incluso; la prima fascia parte da 0 escluso)."""
    minimo, massimo = ESTREMI_BUDGET[fascia]
    return importo > minimo and (massimo is None or importo <= massimo)


# ------------------------------------------------------------ citazioni

_URL_CITAZIONE_MAX = 2048


def _url_citazione(valore: str | None) -> str | None:
    """L'URL di una citazione ammesso dal filtro dei link della scheda
    (`url_documento_pubblicabile`), altrimenti None. None anche se, con gli
    spazi codificati, supera `_URL_CITAZIONE_MAX`: una citazione già filtrata
    si riconvalida sempre (`CitazioneSnapshot`), senza errori."""
    ammesso = url_documento_pubblicabile(valore)
    return ammesso if ammesso is not None and len(ammesso) <= _URL_CITAZIONE_MAX else None


class CitazioneIn(BaseModel):
    """Ancoraggio al testo del bando di un requisito o di una voce delle
    regole. Stessa forma di `CitazioneRegolaOut` (WP3), con `fonte_etichetta`
    facoltativa per le citazioni dell'AI-check. Solo testo semplice: l'URL
    solo https (altrimenti errore) e, già in ingresso (`RequisitoIn`),
    ammesso dal filtro dei link della scheda: un URL https non ammesso
    diventa None, mai un errore."""

    model_config = ConfigDict(extra="forbid")

    sezione: str = Field(max_length=40)
    testo: str = Field(max_length=MAX_TESTO_VOCE)
    verificata: StrictBool = False
    fonte_etichetta: str | None = Field(default=None, max_length=300)
    url_documento: str | None = Field(default=None, max_length=_URL_CITAZIONE_MAX)
    pagina: int | None = Field(default=None, ge=1)

    @field_validator("url_documento")
    @classmethod
    def _https(cls, valore: str | None) -> str | None:
        if valore is not None and not valore.lower().startswith("https://"):
            raise ValueError("url_documento deve essere https")
        return _url_citazione(valore)


class CitazioneSnapshotOut(CitazioneIn):
    """`CitazioneIn` come esce dall'API: requisiti, voci e regole finanziarie
    dello snapshot, regole di origine del validatore del consorzio. L'URL del
    documento ripassa dal filtro dei link della scheda
    (`url_documento_pubblicabile`), che vale pure per le righe salvate prima
    del filtro: un URL non ammesso, `http`, più lungo di `_URL_CITAZIONE_MAX`
    o non stringa diventa None, mai un errore. Lo snapshot è anche il body
    della conferma delle regole (`RegoleConfermaIn`): lì vale la stessa
    regola, senza 422."""

    # Senza `max_length`: un URL troppo lungo lo scarta il validatore.
    url_documento: str | None = None

    # Stesso nome del validatore di `CitazioneIn`, che così non si eredita
    # (con un nome diverso varrebbero entrambi, e `http` sarebbe un errore).
    @field_validator("url_documento", mode="before")
    @classmethod
    def _https(cls, valore: Any) -> str | None:
        return _url_citazione(valore) if isinstance(valore, str) else None


def _come_snapshot(valore: Any) -> Any:
    """Accetta anche una `CitazioneIn` già costruita (gap analysis, regole del
    validatore del consorzio): si riconvalida come `CitazioneSnapshotOut`."""
    if isinstance(valore, CitazioneIn) and not isinstance(valore, CitazioneSnapshotOut):
        return valore.model_dump()
    return valore


CitazioneSnapshot = Annotated[CitazioneSnapshotOut, BeforeValidator(_come_snapshot)]


# ------------------------------------------------- snapshot delle regole


class _VoceSnapshot(BaseModel):
    """Una voce delle regole confermate. `confermata` solo per voci che
    nell'estrazione erano `verificata`, con la loro citazione verificata (il
    servizio lo ricontrolla sulla riga `bando_partenariato` corrente);
    `modificata` = responsabilità del creatore, citazione facoltativa;
    `aggiunta` = senza citazione."""

    model_config = ConfigDict(extra="forbid")

    origine_voce: OrigineVoce
    citazione: CitazioneSnapshot | None = None

    @model_validator(mode="after")
    def _citazione_coerente(self):
        if self.origine_voce == "confermata" and (
            self.citazione is None or not self.citazione.verificata
        ):
            raise BadRequestError(
                "Una regola si può confermare così com'è solo se il passaggio del bando è "
                "stato ritrovato: altrimenti modificala"
            )
        if self.origine_voce == "aggiunta" and self.citazione is not None:
            raise BadRequestError("Una regola aggiunta da te non ha una citazione del bando")
        return self


class _VoceConId(_VoceSnapshot):
    id: str = Field(min_length=1, max_length=ID_VOCE_MAX)


class ModalitaSnapshot(_VoceSnapshot):
    valore: Modalita

    @field_validator("valore")
    @classmethod
    def _ammette(cls, valore: str) -> str:
        if valore == "non_ammesso":
            raise BadRequestError(
                "Una call di partenariato richiede un bando che ammetta il partenariato"
            )
        return valore


class CostituzioneSnapshot(_VoceSnapshot):
    valore: Costituzione


class ConteggioSnapshot(_VoceSnapshot):
    valore: int

    @field_validator("valore")
    @classmethod
    def _plausibile(cls, valore: int) -> int:
        if not 1 <= valore <= 100:
            raise BadRequestError("Il numero di partner deve essere tra 1 e 100")
        return valore


class FormaAmmessaSnapshot(_VoceSnapshot):
    forma: FormaAggregazione
    note: str | None = None

    @field_validator("note")
    @classmethod
    def _note(cls, valore: str | None) -> str | None:
        return _testo(valore, "La nota della forma", massimo=MAX_TESTO_BREVE)


class ComposizioneSnapshot(_VoceConId):
    tipo_soggetto: TipoSoggetto
    tipo_soggetto_testo: str | None = None
    minimo: int | None = None
    massimo: int | None = None
    ruolo: RuoloComposizione
    regioni: list[int] = Field(default_factory=list)
    paesi: list[str] = Field(default_factory=list)
    vincolo_territoriale: str | None = None

    @field_validator("tipo_soggetto_testo")
    @classmethod
    def _tipo_testo(cls, valore: str | None) -> str | None:
        return _testo(valore, "La descrizione del tipo di soggetto", massimo=300)

    @field_validator("vincolo_territoriale")
    @classmethod
    def _vincolo(cls, valore: str | None) -> str | None:
        return _testo(valore, "Il vincolo territoriale", massimo=MAX_TESTO_BREVE)

    @field_validator("regioni")
    @classmethod
    def _regioni(cls, valori: list[int]) -> list[int]:
        if any(v < 1 for v in valori):
            raise BadRequestError("Regione non valida")
        return _al_massimo(_dedup(valori), 21, "regioni")

    @field_validator("paesi")
    @classmethod
    def _paesi(cls, valori: list[str]) -> list[str]:
        # Testo libero dell'estrazione (nomi o codici): solo ripulito.
        puliti = [" ".join(v.split())[:80] for v in valori if v and v.strip()]
        return _al_massimo(_dedup(puliti), MAX_PAESI_POSIZIONE, "paesi")

    @model_validator(mode="after")
    def _min_max(self):
        for valore in (self.minimo, self.massimo):
            if valore is not None and not 0 <= valore <= 100:
                raise BadRequestError("Nella composizione i numeri devono essere tra 0 e 100")
        if self.minimo is not None and self.massimo is not None and self.minimo > self.massimo:
            raise BadRequestError("Nella composizione il minimo supera il massimo")
        return self


class QuotaSnapshot(_VoceConId):
    ambito: AmbitoQuota
    categoria: TipoSoggetto | None = None
    min_percentuale: float | None = None
    max_percentuale: float | None = None
    base_calcolo: BaseCalcolo
    effetto_violazione: EffettoViolazione

    @model_validator(mode="after")
    def _coerente(self):
        for valore in (self.min_percentuale, self.max_percentuale):
            if valore is not None and not (math.isfinite(valore) and 0 <= valore <= 100):
                raise BadRequestError("Le percentuali della quota devono essere tra 0 e 100")
        if self.min_percentuale is None and self.max_percentuale is None:
            raise BadRequestError("Indica almeno una percentuale per la quota")
        if (
            self.min_percentuale is not None
            and self.max_percentuale is not None
            and self.min_percentuale > self.max_percentuale
        ):
            raise BadRequestError("Nella quota la percentuale minima supera la massima")
        if self.ambito == "per_categoria" and self.categoria is None:
            raise BadRequestError("Una quota per categoria deve indicare la categoria")
        return self


class VincoloSnapshot(_VoceConId):
    tipo: TipoVincolo
    descrizione: str
    parametro: float | None = None
    momento: Momento

    @field_validator("parametro")
    @classmethod
    def _parametro(cls, valore: float | None) -> float | None:
        if valore is not None and not (math.isfinite(valore) and valore >= 0):
            raise BadRequestError("Il parametro del vincolo non è plausibile")
        return valore

    @field_validator("descrizione")
    @classmethod
    def _descrizione(cls, valore: str) -> str:
        return _testo(
            valore, "La descrizione del vincolo", minimo=3, massimo=MAX_TESTO_VOCE,
            vuoto_none=False,
        )


class RegolaFinanziariaSnapshot(RegolaFinanziaria):
    """Regola finanziaria confermata. Q11 (divieto di regole finanziarie
    manuali): entra nello snapshot SOLO come `confermata`, cioè una voce
    verificata dell'estrazione con la sua citazione; niente `modificata` né
    `aggiunta`. La coerenza la verifica `bilanci_indicatori.valida_regola`."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=ID_VOCE_MAX)
    origine_voce: OrigineVoce
    citazione: CitazioneSnapshot | None = None

    @field_validator("origine_voce")
    @classmethod
    def _solo_confermata(cls, valore: str) -> str:
        if valore != "confermata":
            raise BadRequestError(
                "Le regole finanziarie del bando si possono solo confermare così come sono "
                "o togliere: non si modificano né si aggiungono a mano"
            )
        return valore

    @field_validator("descrizione")
    @classmethod
    def _descrizione(cls, valore: str) -> str:
        return _testo(
            valore, "La descrizione della regola", minimo=3, massimo=MAX_TESTO_VOCE,
            vuoto_none=False,
        )

    @model_validator(mode="after")
    def _coerente(self):
        # Import locale: gli schemi non dipendono dai servizi al caricamento.
        from app.services.bilanci_indicatori import valida_regola

        if self.citazione is None or not self.citazione.verificata:
            raise BadRequestError(
                "Una regola finanziaria si conferma solo con il passaggio del bando ritrovato"
            )
        errori = valida_regola(self)
        if errori:
            raise BadRequestError("Regola finanziaria non valida: " + "; ".join(errori))
        return self

    def regola(self) -> RegolaFinanziaria:
        """Il contratto WP1 senza i campi dello snapshot."""
        return RegolaFinanziaria(
            **{nome: getattr(self, nome) for nome in RegolaFinanziaria.model_fields}
        )


class DocumentoSnapshot(_VoceConId):
    tipo: TipoDocumentoRichiesto
    descrizione: str
    momento: Momento

    @field_validator("descrizione")
    @classmethod
    def _descrizione(cls, valore: str) -> str:
        return _testo(
            valore, "La descrizione del documento", minimo=3, massimo=MAX_TESTO_VOCE,
            vuoto_none=False,
        )


class FonteSnapshot(BaseModel):
    """Da quale estrazione WP3 viene lo snapshot: la scrive il SERVIZIO dalla
    riga `bando_partenariato` corrente (quella inviata dal client si ignora)."""

    model_config = ConfigDict(extra="forbid")

    estratta_at: datetime | None = None
    prompt_version: int | None = None
    modalita_effettiva: Modalita


class RegoleCallSnapshot(BaseModel):
    """`partner_calls.regole_partenariato`: le regole del bando come le ha
    confermate o corrette il creatore (C2). Stessi campi delle voci di
    `RegolePartenariatoOut` (WP3), senza `stato`/`avvisi` e con
    `origine_voce`. Una voce `da_verificare` dell'estrazione entra solo come
    `modificata`; una voce non riportata qui non vale."""

    model_config = ConfigDict(extra="forbid")

    versione: Literal[1] = 1
    fonte: FonteSnapshot | None = None
    modalita: ModalitaSnapshot
    forme_ammesse: list[FormaAmmessaSnapshot] = Field(default_factory=list)
    costituzione: CostituzioneSnapshot | None = None
    partner_min: ConteggioSnapshot | None = None
    partner_max: ConteggioSnapshot | None = None
    composizione: list[ComposizioneSnapshot] = Field(default_factory=list)
    quote: list[QuotaSnapshot] = Field(default_factory=list)
    vincoli: list[VincoloSnapshot] = Field(default_factory=list)
    regole_finanziarie: list[RegolaFinanziariaSnapshot] = Field(default_factory=list)
    documenti_richiesti: list[DocumentoSnapshot] = Field(default_factory=list)

    @model_validator(mode="after")
    def _coerenza(self):
        for nome in (
            "forme_ammesse", "composizione", "quote", "vincoli", "regole_finanziarie",
            "documenti_richiesti",
        ):
            _al_massimo(getattr(self, nome), MAX_VOCI_SNAPSHOT, "voci per sezione")
        forme = [f.forma for f in self.forme_ammesse]
        if len(forme) != len(set(forme)):
            raise BadRequestError("Ogni forma di aggregazione va indicata una sola volta")
        for nome in (
            "composizione", "quote", "vincoli", "regole_finanziarie", "documenti_richiesti"
        ):
            ids = [voce.id for voce in getattr(self, nome)]
            if len(ids) != len(set(ids)):
                raise BadRequestError("Due voci delle regole hanno lo stesso identificativo")
        if (
            self.partner_min is not None
            and self.partner_max is not None
            and self.partner_min.valore > self.partner_max.valore
        ):
            raise BadRequestError("Il numero minimo di partner supera il massimo")
        return self


# ----------------------------------------------------------------- input


class CallCreaIn(BaseModel):
    """POST /partenariati/call: la bozza nasce dal bando."""

    model_config = ConfigDict(extra="forbid")

    bando_slug: str = Field(min_length=1, max_length=255)
    ruolo_creatore: RuoloCreatore
    forma_aggregazione_prevista: FormaPrevista | None = None
    # Richiesto solo se l'estrazione WP3 dice `non_ammesso` (409
    # `partenariato_non_ammesso` altrimenti): perché il bando lo ammette.
    override_non_ammesso_motivo: str | None = None
    # false solo per un'azienda con l'identità verificata dalla piattaforma
    # (WP9): altrimenti 409 `identita_non_verificata_admin` dal servizio.
    anonima: StrictBool = True

    @field_validator("override_non_ammesso_motivo")
    @classmethod
    def _override(cls, valore: str | None) -> str | None:
        return _testo(
            valore, "Il motivo", minimo=OVERRIDE_MIN, massimo=OVERRIDE_MAX
        )


class CallAggiornaIn(BaseModel):
    """PATCH /partenariati/call/{id}: aggiornamento PARZIALE, si usano solo i
    campi presenti (`model_dump(exclude_unset=True)`). `null` azzera un campo
    facoltativo; i campi obbligatori (ruolo, visibilità, anonimato, passo)
    non accettano null (422). In `pubblicata` la RPC ammette solo la whitelist
    (descrizione_pubblica, dettagli_riservati, profilo_partner_ideale,
    scadenza_call, visibilita, budget_fascia, budget_progetto_eur,
    quota_creatore_pct): gli altri campi → 400 `campo_non_modificabile`."""

    model_config = ConfigDict(extra="forbid")

    titolo: str | None = None
    descrizione_pubblica: str | None = None
    # Visibili solo dopo l'accettazione (WP7): possono nominare l'azienda, mai
    # contatti diretti (controllo del servizio).
    dettagli_riservati: str | None = None
    profilo_partner_ideale: str | None = None
    scadenza_call: date | None = None
    visibilita: Visibilita = "pubblica"
    budget_fascia: BudgetFascia | None = None
    # RISERVATO: lo vede solo il creatore; alimenta le sue regole finanziarie.
    budget_progetto_eur: Decimal | None = None
    quota_creatore_pct: Decimal | None = None
    ruolo_creatore: RuoloCreatore = "capofila"
    forma_aggregazione_prevista: FormaPrevista | None = None
    anonima: StrictBool = True
    wizard_passo: int = Field(default=1, ge=1, le=7)
    override_non_ammesso_motivo: str | None = None

    @field_validator("titolo")
    @classmethod
    def _titolo(cls, valore: str | None) -> str | None:
        return _testo(valore, "Il titolo", minimo=TITOLO_MIN, massimo=TITOLO_MAX)

    @field_validator("descrizione_pubblica")
    @classmethod
    def _descrizione(cls, valore: str | None) -> str | None:
        return _testo(valore, "La descrizione pubblica", massimo=MAX_DESCRIZIONE_PUBBLICA)

    @field_validator("dettagli_riservati")
    @classmethod
    def _riservati(cls, valore: str | None) -> str | None:
        return _testo(valore, "I dettagli riservati", massimo=MAX_DETTAGLI_RISERVATI)

    @field_validator("profilo_partner_ideale")
    @classmethod
    def _profilo(cls, valore: str | None) -> str | None:
        return _testo(valore, "Il profilo del partner ideale", massimo=MAX_PROFILO_IDEALE)

    @field_validator("override_non_ammesso_motivo")
    @classmethod
    def _override(cls, valore: str | None) -> str | None:
        return _testo(valore, "Il motivo", minimo=OVERRIDE_MIN, massimo=OVERRIDE_MAX)

    @field_validator("budget_progetto_eur")
    @classmethod
    def _budget(cls, valore: Decimal | None) -> Decimal | None:
        if valore is None:
            return None
        if not valore.is_finite() or valore <= 0 or valore > MAX_BUDGET_EUR:
            raise BadRequestError("Il budget del progetto deve essere un importo positivo")
        if valore.as_tuple().exponent < -2:
            raise BadRequestError("Il budget del progetto può avere al massimo due decimali")
        return valore

    @field_validator("quota_creatore_pct")
    @classmethod
    def _quota(cls, valore: Decimal | None) -> Decimal | None:
        return _percentuale(valore, "La tua quota")

    @model_validator(mode="after")
    def _coerenza(self):
        if (
            self.budget_fascia is not None
            and self.budget_progetto_eur is not None
            and not budget_nella_fascia(self.budget_fascia, self.budget_progetto_eur)
        ):
            raise BadRequestError("Il budget del progetto non rientra nella fascia scelta")
        return self

    def campi(self) -> dict[str, Any]:
        """I soli campi inviati, in forma JSON (per `p_campi` della RPC)."""
        return self.model_dump(mode="json", exclude_unset=True)


class RegoleConfermaIn(BaseModel):
    """POST /partenariati/call/{id}/regole (solo in bozza)."""

    model_config = ConfigDict(extra="forbid")

    regole: RegoleCallSnapshot
    esclusivita: StrictBool


class RequisitoIn(BaseModel):
    """Un requisito della call (replace-all con PUT). `id` conserva la
    riga (le posizioni vi rimandano con `requisiti_ids`); l'etichetta breve
    («A», «B», …) la assegna la RPC in ordine (quella indicata qui vale solo
    per un requisito conservato, con `id`); la copertura del creatore la
    calcola il servizio, mai il client."""

    model_config = ConfigDict(extra="forbid")

    id: UUID | None = None
    etichetta: str | None = None
    testo: str
    criterio: CriterioPartner | None = None
    ambito: AmbitoRequisito = "consorzio"
    cercato: StrictBool = False
    origine: OrigineRequisito = "manuale"
    rif_origine: str | None = None
    citazione: CitazioneIn | None = None

    @field_validator("etichetta")
    @classmethod
    def _etichetta(cls, valore: str | None) -> str | None:
        return _testo(valore, "L'etichetta del requisito", massimo=ETICHETTA_MAX)

    @field_validator("testo")
    @classmethod
    def _testo_requisito(cls, valore: str) -> str:
        return _testo(
            valore, "Il testo del requisito", minimo=TESTO_REQUISITO_MIN,
            massimo=TESTO_REQUISITO_MAX, vuoto_none=False,
        )

    @field_validator("rif_origine")
    @classmethod
    def _rif(cls, valore: str | None) -> str | None:
        if valore is not None and len(valore) > RIF_ORIGINE_MAX:
            raise BadRequestError("Riferimento del requisito troppo lungo")
        return valore

    @model_validator(mode="after")
    def _regola_solo_dal_bando(self):
        # Q11: nessuna regola finanziaria scritta a mano. Si ammette solo
        # quella di una regola confermata dello snapshot (il servizio verifica
        # che `rif_origine` e la regola coincidano: partner_call_gap
        # .errori_regole_finanziarie).
        regola = isinstance(self.criterio, CriterioRegolaFinanziaria)
        if regola != (self.origine == "regola_finanziaria") or (
            regola and not self.rif_origine
        ):
            raise BadRequestError(
                "Le regole finanziarie vengono solo dalle regole del bando confermate"
            )
        return self


class RequisitiIn(BaseModel):
    """PUT /partenariati/call/{id}/requisiti."""

    model_config = ConfigDict(extra="forbid")

    requisiti: list[RequisitoIn] = Field(default_factory=list)

    @field_validator("requisiti")
    @classmethod
    def _quanti(cls, valori: list[RequisitoIn]) -> list[RequisitoIn]:
        ids = [r.id for r in valori if r.id is not None]
        if len(ids) != len(set(ids)):
            raise BadRequestError("Due requisiti hanno lo stesso identificativo")
        return _al_massimo(valori, MAX_REQUISITI, "requisiti")


class PosizioneIn(BaseModel):
    """Una posizione cercata (replace-all con PUT). Le colonne di
    `partner_call_posizioni`; `id` conserva la riga."""

    model_config = ConfigDict(extra="forbid")

    id: UUID | None = None
    titolo: str
    ruolo: RuoloPartenariato = "partner"
    tipi_soggetto: list[TipoSoggetto] = Field(default_factory=list)
    competenze: list[Competenza] = Field(default_factory=list)
    ateco_divisioni: list[str] = Field(default_factory=list)
    regioni: list[int] = Field(default_factory=list)
    territorio_modalita: TerritorioModalita = "qualsiasi"
    paesi: list[str] = Field(default_factory=list)
    dimensioni: list[Dimensione] = Field(default_factory=list)
    quota_ipotizzata_pct: Decimal | None = None
    numero: int = Field(default=1, ge=1, le=NUMERO_MAX_POSIZIONE)
    requisiti_ids: list[UUID] = Field(default_factory=list)
    note: str | None = None

    @field_validator("titolo")
    @classmethod
    def _titolo(cls, valore: str) -> str:
        return _testo(
            valore, "Il titolo della posizione", minimo=TITOLO_POSIZIONE_MIN,
            massimo=TITOLO_POSIZIONE_MAX, vuoto_none=False,
        )

    @field_validator("note")
    @classmethod
    def _note(cls, valore: str | None) -> str | None:
        return _testo(valore, "La nota della posizione", massimo=MAX_NOTE_POSIZIONE)

    @field_validator("tipi_soggetto")
    @classmethod
    def _tipi(cls, valori: list[str]) -> list[str]:
        return _al_massimo(_dedup(valori), MAX_TIPI_POSIZIONE, "tipi di soggetto per posizione")

    @field_validator("competenze")
    @classmethod
    def _competenze(cls, valori: list[str]) -> list[str]:
        return _al_massimo(_dedup(valori), MAX_COMPETENZE_POSIZIONE, "competenze per posizione")

    @field_validator("ateco_divisioni")
    @classmethod
    def _ateco(cls, valori: list[str]) -> list[str]:
        for valore in valori:
            if len(valore) != 2 or not valore.isdigit():
                raise BadRequestError("Le divisioni ATECO hanno due cifre (es. 62)")
        return _al_massimo(_dedup(valori), MAX_DIVISIONI_POSIZIONE, "divisioni ATECO")

    @field_validator("regioni")
    @classmethod
    def _regioni(cls, valori: list[int]) -> list[int]:
        if any(v < 1 for v in valori):
            raise BadRequestError("Regione non valida")
        return _al_massimo(_dedup(valori), MAX_REGIONI_POSIZIONE, "regioni")

    @field_validator("paesi")
    @classmethod
    def _paesi(cls, valori: list[str]) -> list[str]:
        return _al_massimo(
            _dedup([paese_iso2(v) for v in valori]), MAX_PAESI_POSIZIONE, "paesi"
        )

    @field_validator("dimensioni")
    @classmethod
    def _dimensioni(cls, valori: list[str]) -> list[str]:
        return _dedup(valori)

    @field_validator("quota_ipotizzata_pct")
    @classmethod
    def _quota(cls, valore: Decimal | None) -> Decimal | None:
        return _percentuale(valore, "La quota ipotizzata")

    @field_validator("requisiti_ids")
    @classmethod
    def _requisiti(cls, valori: list[UUID]) -> list[UUID]:
        return _al_massimo(_dedup(valori), MAX_REQUISITI_POSIZIONE, "requisiti per posizione")

    @model_validator(mode="after")
    def _territorio(self):
        if self.territorio_modalita != "qualsiasi" and not self.regioni:
            raise BadRequestError("Indica le regioni in cui serve la sede")
        if self.territorio_modalita == "qualsiasi" and self.regioni:
            raise BadRequestError(
                "Indica se la sede nella regione serve già ora o entro l'erogazione"
            )
        return self


class PosizioniIn(BaseModel):
    """PUT /partenariati/call/{id}/posizioni."""

    model_config = ConfigDict(extra="forbid")

    posizioni: list[PosizioneIn] = Field(default_factory=list)

    @field_validator("posizioni")
    @classmethod
    def _quante(cls, valori: list[PosizioneIn]) -> list[PosizioneIn]:
        ids = [p.id for p in valori if p.id is not None]
        if len(ids) != len(set(ids)):
            raise BadRequestError("Due posizioni hanno lo stesso identificativo")
        return _al_massimo(valori, MAX_POSIZIONI, "posizioni")


class ChiudiIn(BaseModel):
    """POST /partenariati/call/{id}/chiudi."""

    model_config = ConfigDict(extra="forbid")

    esito: Literal["completata", "annullata"]


class SegnalazioneIn(BaseModel):
    """POST /partenariati/segnalazioni (DSA, art. 16): la dichiarazione di
    buona fede è obbligatoria e deve essere proprio `true`."""

    model_config = ConfigDict(extra="forbid")

    oggetto_tipo: OggettoSegnalazione
    # Id della call o codice pubblico del profilo (uuid) oppure, dal WP7, id
    # del messaggio della chat (intero positivo): testo nella tabella.
    oggetto_id: str
    motivo: MotivoSegnalazione
    descrizione: str
    buona_fede: Literal[True]

    @field_validator("buona_fede", mode="before")
    @classmethod
    def _buona_fede(cls, valore: Any) -> Any:
        if valore is False:
            raise BadRequestError(
                "Per inviare la segnalazione devi dichiarare che è fatta in buona fede"
            )
        if valore is not True:
            raise ValueError("buona_fede deve essere true")
        return valore

    @field_validator("oggetto_id")
    @classmethod
    def _oggetto(cls, valore: str, info: ValidationInfo) -> str:
        if info.data.get("oggetto_tipo") == "messaggio":
            if not re.fullmatch(r"[0-9]{1,18}", valore.strip()) or int(valore) <= 0:
                raise BadRequestError("Contenuto da segnalare non valido")
            return str(int(valore))
        try:
            return str(UUID(valore.strip()))
        except ValueError:
            raise BadRequestError("Contenuto da segnalare non valido") from None

    @field_validator("descrizione")
    @classmethod
    def _descrizione(cls, valore: str) -> str:
        return _testo(
            valore, "La descrizione", minimo=SEGNALAZIONE_MIN, massimo=SEGNALAZIONE_MAX,
            vuoto_none=False,
        )


# ------------------------------------------------------------ vista creatore


class BandoCallOut(BaseModel):
    """Snapshot del bando salvato nella call."""

    id: int
    slug: str
    titolo: str
    scadenza: date | None = None
    programma_id: int | None = None
    tipologia_id: int | None = None
    stato_effettivo: str | None = None
    verificato_at: datetime | None = None
    mancante_dal: date | None = None


class RequisitoOut(BaseModel):
    """Requisito con la copertura del creatore. `id` è None per le proposte
    della gap analysis non ancora salvate. `copertura_nota` viene SOLO da
    template deterministici (mai da testo del modello) e non esce mai verso
    terzi, come tutta la copertura del creatore."""

    id: UUID | None = None
    etichetta: str | None = None
    testo: str
    criterio: CriterioPartner | None = None
    ambito: AmbitoRequisito = "consorzio"
    cercato: bool = False
    origine: OrigineRequisito
    rif_origine: str | None = None
    citazione: CitazioneSnapshot | None = None
    copertura_creatore: EsitoCopertura | None = None
    copertura_fonte: FonteCopertura | None = None
    copertura_nota: str | None = None
    ordine: int = 0


class PosizioneOut(BaseModel):
    """Posizione come salvata (lettura: tipi larghi, i dati sono già passati
    dalla validazione di `PosizioneIn`)."""

    id: UUID
    titolo: str
    ruolo: str = "partner"
    tipi_soggetto: list[str] = Field(default_factory=list)
    competenze: list[str] = Field(default_factory=list)
    ateco_divisioni: list[str] = Field(default_factory=list)
    regioni: list[int] = Field(default_factory=list)
    territorio_modalita: str = "qualsiasi"
    paesi: list[str] = Field(default_factory=list)
    dimensioni: list[str] = Field(default_factory=list)
    quota_ipotizzata_pct: Decimal | None = None
    numero: int = 1
    requisiti_ids: list[UUID] = Field(default_factory=list)
    note: str | None = None
    ordine: int = 0


class RiepilogoGapOut(BaseModel):
    coperti: int = 0
    non_coperti: int = 0
    dato_mancante: int = 0
    incerto: int = 0
    non_valutabile: int = 0


class AiCheckGapOut(BaseModel):
    """L'ultimo AI-check `ready` dell'azienda sul bando. Senza, la UI propone
    di lanciarlo avvisando che consuma la quota."""

    disponibile: bool = False
    id: UUID | None = None
    data: datetime | None = None


class PartenariatoGapOut(BaseModel):
    stato: StatoPartenariato | None = None
    modalita_effettiva: Modalita | None = None


class GapOut(BaseModel):
    requisiti: list[RequisitoOut] = Field(default_factory=list)
    riepilogo: RiepilogoGapOut = Field(default_factory=RiepilogoGapOut)
    ai_check: AiCheckGapOut = Field(default_factory=AiCheckGapOut)
    partenariato: PartenariatoGapOut = Field(default_factory=PartenariatoGapOut)


class RilievoOut(BaseModel):
    """Rilievo anti-contatti su un testo pubblico: nomina campo e tipo; per
    i dati dell'azienda stessa l'estratto si mostra solo al creatore."""

    campo: str
    tipo: str
    estratto: str
    bloccante: bool = True


class PosizioneProposta(BaseModel):
    """Posizione proposta dall'AI, già post-validata (codici del vocabolario,
    regioni della lookup, riferimenti noti): si salva solo se il creatore la
    conferma con PUT /posizioni. `requisiti_ids` rimandano ai requisiti
    salvati della call."""

    titolo: str
    ruolo: RuoloPartenariato = "partner"
    tipi_soggetto: list[TipoSoggetto] = Field(default_factory=list)
    competenze: list[Competenza] = Field(default_factory=list)
    ateco_divisioni: list[str] = Field(default_factory=list)
    regioni: list[int] = Field(default_factory=list)
    territorio_modalita: TerritorioModalita = "qualsiasi"
    paesi: list[str] = Field(default_factory=list)
    dimensioni: list[Dimensione] = Field(default_factory=list)
    quota_ipotizzata_pct: Decimal | None = None
    numero: int = 1
    requisiti_ids: list[UUID] = Field(default_factory=list)
    note: str | None = None
    motivazione: str | None = None


class PropostaPosizioniOut(BaseModel):
    posizioni: list[PosizioneProposta] = Field(default_factory=list)
    # Avvisi deterministici (quote che superano il 100%, minimo/massimo di
    # partner del bando, riferimenti scartati…).
    avvisi: list[str] = Field(default_factory=list)


class PropostaTestiOut(BaseModel):
    """Bozza dei testi pubblici: già anonimizzata, con i rilievi rimasti."""

    titolo: str | None = None
    descrizione_pubblica: str | None = None
    profilo_partner_ideale: str | None = None
    rilievi: list[RilievoOut] = Field(default_factory=list)
    avvisi: list[str] = Field(default_factory=list)


class JobAiOut(BaseModel):
    """Stato di un job AI asincrono della call (202 + poll-on-read). La
    proposta non si salva mai da sola: il creatore la applica e salva."""

    stato: StatoJobAi = "nessuno"
    avviata_at: datetime | None = None
    errore: str | None = None
    proposta: PropostaPosizioniOut | PropostaTestiOut | None = None


class JobPosizioniOut(JobAiOut):
    proposta: PropostaPosizioniOut | None = None


class JobTestiOut(JobAiOut):
    proposta: PropostaTestiOut | None = None


class MotivoBloccoOut(BaseModel):
    """Perché la call non si può pubblicare ora (codice macchina + testo)."""

    codice: str
    messaggio: str


class CallVistaCreatoreOut(BaseModel):
    """GET /partenariati/call/{id} per l'azienda creatrice (titolare e membri
    con visibilità: `editable` solo per il titolare). Mai `family_parent_id`
    né `creato_da`."""

    id: UUID
    company_profile_id: UUID
    editable: bool
    stato: StatoCall
    motivo_chiusura: MotivoChiusura | None = None
    versione: int = 0
    wizard_passo: int = 1
    bando: BandoCallOut
    ruolo_creatore: RuoloCreatore
    forma_aggregazione_prevista: FormaPrevista | None = None
    anonima: bool = True
    titolo: str | None = None
    descrizione_pubblica: str | None = None
    dettagli_riservati: str | None = None
    profilo_partner_ideale: str | None = None
    budget_fascia: BudgetFascia | None = None
    budget_progetto_eur: Decimal | None = None
    quota_creatore_pct: Decimal | None = None
    scadenza_call: date | None = None
    visibilita: Visibilita = "pubblica"
    override_non_ammesso_motivo: str | None = None
    regole_partenariato: RegoleCallSnapshot | None = None
    regole_confermate_at: datetime | None = None
    esclusivita: bool = False
    posizioni: list[PosizioneOut] = Field(default_factory=list)
    gap: GapOut = Field(default_factory=GapOut)
    ai_posizioni: JobPosizioniOut = Field(default_factory=JobPosizioniOut)
    ai_testi: JobTestiOut = Field(default_factory=JobTestiOut)
    # Pool del titolare (fn_partenariati_snapshot); None se non leggibile.
    limiti: PartenariatiEntitlement | None = None
    puo_pubblicare: bool = False
    motivi_blocco: list[MotivoBloccoOut] = Field(default_factory=list)
    pubblicata_at: datetime | None = None
    chiusa_at: datetime | None = None
    sospesa_at: datetime | None = None
    sospeso_motivo: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# --------------------------------------------------- proiezioni per terzi


class _Pubblico(BaseModel):
    # Whitelist anche nella costruzione: un campo non previsto è un errore.
    model_config = ConfigDict(extra="forbid")


class CreatoreCallOut(_Pubblico):
    """Il creatore visto da terzi. Call anonima (C3): «Azienda anonima» con
    regione della sede, sezione ATECO e classe dimensionale, tutto dal
    registro; niente fasce, niente coperture, niente identificativi. Call
    nominativa di un'azienda con l'identità verificata OGGI (WP9): `anonima`
    false e `denominazione` = denominazione del Registro Imprese, nient'altro
    (mai P.IVA, sito, PEC né persone)."""

    anonima: bool = True
    denominazione: str = "Azienda anonima"
    regione: str | None = None
    ateco_sezione: AtecoSezioneOut | None = None
    classe_dimensionale: Dimensione | None = None


class BandoPubblicoCallOut(_Pubblico):
    slug: str
    titolo: str
    scadenza: date | None = None


class RequisitoPubblicoOut(_Pubblico):
    """Requisito CERCATO (C3): etichetta, testo e criterio; niente copertura
    del creatore, niente citazione, niente id della riga."""

    etichetta: str
    testo: str
    criterio: CriterioPartner | None = None
    ambito: AmbitoRequisito = "consorzio"


class PosizionePubblicaOut(_Pubblico):
    """Posizione verso terzi; i requisiti per etichetta («A», «C»), solo tra
    quelli cercati."""

    id: UUID
    titolo: str
    ruolo: RuoloPartenariato = "partner"
    tipi_soggetto: list[TipoSoggetto] = Field(default_factory=list)
    competenze: list[Competenza] = Field(default_factory=list)
    ateco_divisioni: list[str] = Field(default_factory=list)
    regioni: list[int] = Field(default_factory=list)
    regioni_nomi: list[str] = Field(default_factory=list)
    territorio_modalita: TerritorioModalita = "qualsiasi"
    paesi: list[str] = Field(default_factory=list)
    dimensioni: list[Dimensione] = Field(default_factory=list)
    quota_ipotizzata_pct: Decimal | None = None
    numero: int = 1
    requisiti: list[str] = Field(default_factory=list)
    note: str | None = None


class CallPubblicaOut(_Pubblico):
    """La call vista dalle altre aziende (e l'anteprima «come ti vedono»).
    `id` è l'uuid della call (non identifica l'azienda). MAI:
    `company_profile_id`, `family_parent_id`, `creato_da`,
    `budget_progetto_eur`, `dettagli_riservati`, coperture del creatore,
    fasce del creatore, regole modificate dal creatore."""

    id: UUID
    stato: StatoCall
    bando: BandoPubblicoCallOut
    creatore: CreatoreCallOut
    ruolo_creatore: RuoloCreatore
    forma_aggregazione_prevista: FormaPrevista | None = None
    titolo: str | None = None
    descrizione_pubblica: str | None = None
    profilo_partner_ideale: str | None = None
    budget_fascia: BudgetFascia | None = None
    scadenza_call: date | None = None
    visibilita: Visibilita = "pubblica"
    esclusivita: bool = False
    pubblicata_at: datetime | None = None
    requisiti: list[RequisitoPubblicoOut] = Field(default_factory=list)
    posizioni: list[PosizionePubblicaOut] = Field(default_factory=list)


class CallCardOut(_Pubblico):
    """Una call nelle liste (le mie; dal WP6 anche bacheca e salvate). Per le
    call dell'azienda attiva (`mia`) anche passo del wizard e ultimo
    aggiornamento; per le altre restano null."""

    id: UUID
    stato: StatoCall
    titolo: str | None = None
    bando: BandoPubblicoCallOut
    creatore: CreatoreCallOut
    ruolo_creatore: RuoloCreatore
    budget_fascia: BudgetFascia | None = None
    scadenza_call: date | None = None
    pubblicata_at: datetime | None = None
    posizioni_n: int = 0
    requisiti_cercati_n: int = 0
    mia: bool = False
    wizard_passo: int | None = None
    updated_at: datetime | None = None


class AnteprimaOut(BaseModel):
    """GET /partenariati/call/{id}/anteprima: come la vedono gli altri, più
    i rilievi anti-contatti sui testi pubblici."""

    call: CallPubblicaOut
    rilievi: list[RilievoOut] = Field(default_factory=list)


class VersioneOut(BaseModel):
    """Una versione pubblicata (partner_call_versioni), solo per il creatore."""

    versione: int
    created_at: datetime
    snapshot: dict[str, Any] = Field(default_factory=dict)


class SegnalazioneOut(BaseModel):
    """Conferma di ricezione (DSA art. 16 c.4)."""

    id: UUID
    stato: StatoSegnalazione
    created_at: datetime

