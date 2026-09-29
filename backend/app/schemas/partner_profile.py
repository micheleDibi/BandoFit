"""Contratti del profilo partner (WP4, docs/partenariati.md §2.4).

Tre famiglie:
  * input (`PartnerProfileIn`, `ConsensoIn`, `ReferenteIn`,
    `ReferenteRispostaIn`): `extra='forbid'`. Le regole di DOMINIO (codici del
    vocabolario, cardinalità come la DDL di 0035, paesi ISO, anni, lunghezze)
    sollevano `BadRequestError` → 400 `bad_request` con un messaggio per
    l'utente: un'eccezione che non è `ValueError` attraversa Pydantic e FastAPI
    la consegna all'handler di `AppError`. Tipi sbagliati e campi sconosciuti
    restano la 422 `validation_error` generica.
  * output del modello: `BozzaProfiloAi`, solo tipi semplici e i codici delle
    competenze come STRINGHE (niente enum da 42 valori né vincoli numerici,
    come lo schema di estrazione WP3: l'API rifiuta le grammatiche troppo
    grandi, budget in tests/test_schemi_ai_dimensione.py): troncamenti, codici
    ignoti e rimozione dei contatti si fanno nel post-processing del servizio
    (`partner_profile_prompts.pulisci_bozza`).
  * DTO: `PartnerProfileOut` (vista del titolare e dei membri),
    `PartnerPubblicoOut` (proiezione a whitelist verso terzi, costruita solo da
    `partner_profilo_pubblico.profilo_pubblico`), `InformativaPartnerOut`.

Le liste di codici si deduplicano mantenendo l'ordine della prima comparsa.
"""

from datetime import date, datetime
from typing import Literal, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from app.core.errors import BadRequestError
from app.schemas.partenariato_vocabolario import (
    Competenza,
    FormaAggregazione,
    RuoloPartenariato,
    TipoSoggetto,
)

# ------------------------------------------------------------------ limiti

# Cardinalità: le stesse CHECK della tabella company_partner_profiles (0035).
MAX_COMPETENZE = 15
MAX_COMPETENZE_LIBERE = 10
MAX_TIPI_SOGGETTO = 8
MAX_SETTORI = 20
MAX_REGIONI = 21
MAX_PAESI = 30
MAX_ESPERIENZE = 20
MAX_CERTIFICAZIONI = 20
MAX_TESTO_LUNGO = 2000  # descrizione_competenze, infrastrutture
# Senza CHECK a DB: tetti prudenti per voci brevi e lookup piccole.
MAX_CATEGORIE_ESCLUSE = 50
MAX_COMPETENZA_LIBERA = 80
MAX_CERTIFICAZIONE = 200
MAX_PROGRAMMA = 120
MAX_TITOLO_ESPERIENZA = 200
ANNO_MINIMO_ESPERIENZA = 1990

# Le forme accettabili dal profilo sono le 7 della DDL: «altra» serve solo
# alle regole estratte dai bandi (WP3), non a una preferenza del partner.
FORME_PROFILO: tuple[str, ...] = tuple(f for f in get_args(FormaAggregazione) if f != "altra")

# Tipi di soggetto che si ricavano SOLO dal Registro Imprese (T5, P3):
# dimensione, sezioni speciali (startup e PMI innovative), albo artigiani,
# forma cooperativa. Non si dichiarano: verso terzi vale il registro.
TIPI_SOGGETTO_DA_REGISTRO: frozenset[str] = frozenset(
    {
        "micro_impresa",
        "piccola_impresa",
        "media_impresa",
        "grande_impresa",
        "pmi",
        "startup_innovativa",
        "pmi_innovativa",
        "impresa_artigiana",
        "cooperativa",
    }
)

_COMPETENZE: frozenset[str] = frozenset(get_args(Competenza))
_TIPI_SOGGETTO: frozenset[str] = frozenset(get_args(TipoSoggetto))
_RUOLI: tuple[str, ...] = get_args(RuoloPartenariato)
_ESITI_ESPERIENZA = ("finanziato", "in_valutazione", "non_finanziato")

# ISO 3166-1 alpha-2 (codici assegnati) più XK (Kosovo, codice provvisorio in
# uso nella UE). EL e UK, le sigle UE di Grecia e Regno Unito, diventano GR e GB.
PAESI_ISO2: frozenset[str] = frozenset(
    """
    AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM
    BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX
    CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG
    GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR
    IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV
    LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE
    NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO
    RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF
    TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF
    WS YE YT ZA ZM ZW XK
    """.split()
)
_ALIAS_PAESI = {"EL": "GR", "UK": "GB"}


# ----------------------------------------------------------------- utilità


def _dedup(valori: list) -> list:
    return list(dict.fromkeys(valori))


def _dedup_testi(valori: list[str]) -> list[str]:
    """Deduplica senza distinguere maiuscole e spazi, tenendo la prima forma."""
    visti: set[str] = set()
    out: list[str] = []
    for valore in valori:
        chiave = " ".join(valore.casefold().split())
        if chiave not in visti:
            visti.add(chiave)
            out.append(valore)
    return out


def _al_massimo(valori: list, massimo: int, cosa: str) -> list:
    if len(valori) > massimo:
        raise BadRequestError(f"Puoi indicare al massimo {massimo} {cosa}")
    return valori


def _codici(valori: list[str], ammessi, cosa: str) -> list[str]:
    for valore in valori:
        if valore not in ammessi:
            raise BadRequestError(f"{cosa} non riconosciuto: «{valore[:40]}»")
    return _dedup(valori)


def _testo_opzionale(valore: str | None, massimo: int, cosa: str) -> str | None:
    if valore is None:
        return None
    pulito = valore.strip()
    if not pulito:
        return None
    if len(pulito) > massimo:
        cifre = f"{massimo:,}".replace(",", ".")
        raise BadRequestError(f"{cosa} può avere al massimo {cifre} caratteri")
    return pulito


def _voci_brevi(valori: list[str], massimo_voce: int, cosa: str) -> list[str]:
    """Voci libere brevi: spazi tolti, vuote scartate, lunghezza massima."""
    out: list[str] = []
    for valore in valori:
        pulito = " ".join(valore.split())
        if not pulito:
            continue
        if len(pulito) > massimo_voce:
            raise BadRequestError(
                f"Ogni voce di {cosa} può avere al massimo {massimo_voce} caratteri"
            )
        out.append(pulito)
    return _dedup_testi(out)


def _id_lookup(valori: list[int], cosa: str) -> list[int]:
    for valore in valori:
        if valore < 1:
            raise BadRequestError(f"{cosa}: valore non valido")
    return _dedup(valori)


# ------------------------------------------------------------------ input


class EsperienzaPartner(BaseModel):
    """Un'esperienza in un programma di finanziamento. `programma_id` rimanda
    alla lookup `programmi` del catalogo (l'esistenza la verifica il servizio)."""

    model_config = ConfigDict(extra="forbid")

    programma: str
    programma_id: int | None = None
    anno: int | None = None
    ruolo: str | None = None
    titolo: str | None = None
    esito: str | None = None

    @field_validator("programma")
    @classmethod
    def _programma(cls, valore: str) -> str:
        pulito = " ".join(valore.split())
        if not pulito:
            raise BadRequestError("Indica il programma di ogni esperienza")
        if len(pulito) > MAX_PROGRAMMA:
            raise BadRequestError(
                f"Il nome del programma può avere al massimo {MAX_PROGRAMMA} caratteri"
            )
        return pulito

    @field_validator("programma_id")
    @classmethod
    def _programma_id(cls, valore: int | None) -> int | None:
        if valore is not None and valore < 1:
            raise BadRequestError("Programma non valido")
        return valore

    @field_validator("anno")
    @classmethod
    def _anno(cls, valore: int | None) -> int | None:
        if valore is None:
            return None
        massimo = date.today().year + 1
        if not ANNO_MINIMO_ESPERIENZA <= valore <= massimo:
            raise BadRequestError(
                f"L'anno di un'esperienza deve essere tra {ANNO_MINIMO_ESPERIENZA} e {massimo}"
            )
        return valore

    @field_validator("ruolo")
    @classmethod
    def _ruolo(cls, valore: str | None) -> str | None:
        if valore is not None and valore not in _RUOLI:
            raise BadRequestError("Il ruolo di un'esperienza deve essere capofila o partner")
        return valore

    @field_validator("titolo")
    @classmethod
    def _titolo(cls, valore: str | None) -> str | None:
        return _testo_opzionale(valore, MAX_TITOLO_ESPERIENZA, "Il titolo di un'esperienza")

    @field_validator("esito")
    @classmethod
    def _esito(cls, valore: str | None) -> str | None:
        if valore is not None and valore not in _ESITI_ESPERIENZA:
            raise BadRequestError("Esito dell'esperienza non riconosciuto")
        return valore


class PartnerProfileIn(BaseModel):
    """Tutti i campi del profilo che il titolare modifica (PUT). NON contiene
    i campi protetti (visibilità, anonimato, consenso, referente, sospensione,
    codice pubblico): cambiano solo tramite le RPC dedicate. I campi assenti
    prendono i default della tabella: il PUT sostituisce il profilo intero."""

    model_config = ConfigDict(extra="forbid")

    descrizione_competenze: str | None = None
    competenze: list[str] = Field(default_factory=list)
    competenze_libere: list[str] = Field(default_factory=list)
    # Solo i tipi DICHIARATI: quelli del registro non si dichiarano.
    tipi_soggetto: list[str] = Field(default_factory=list)
    ruoli_disponibili: list[str] = Field(default_factory=lambda: ["partner"])
    settori_interesse: list[int] = Field(default_factory=list)
    regioni_interesse: list[int] = Field(default_factory=list)
    paesi_interesse: list[str] = Field(default_factory=list)
    forme_accettate: list[str] = Field(default_factory=list)
    esperienze: list[EsperienzaPartner] = Field(default_factory=list)
    certificazioni: list[str] = Field(default_factory=list)
    infrastrutture: str | None = None
    accetta_inviti: bool = True
    categorie_bando_escluse: list[int] = Field(default_factory=list)

    @field_validator("descrizione_competenze")
    @classmethod
    def _descrizione(cls, valore: str | None) -> str | None:
        return _testo_opzionale(valore, MAX_TESTO_LUNGO, "La descrizione delle competenze")

    @field_validator("infrastrutture")
    @classmethod
    def _infrastrutture(cls, valore: str | None) -> str | None:
        return _testo_opzionale(valore, MAX_TESTO_LUNGO, "La descrizione delle infrastrutture")

    @field_validator("competenze")
    @classmethod
    def _competenze(cls, valori: list[str]) -> list[str]:
        codici = _codici(valori, _COMPETENZE, "Competenza")
        return _al_massimo(codici, MAX_COMPETENZE, "competenze")

    @field_validator("competenze_libere")
    @classmethod
    def _competenze_libere(cls, valori: list[str]) -> list[str]:
        voci = _voci_brevi(valori, MAX_COMPETENZA_LIBERA, "competenze aggiuntive")
        return _al_massimo(voci, MAX_COMPETENZE_LIBERE, "competenze aggiuntive")

    @field_validator("tipi_soggetto")
    @classmethod
    def _tipi_soggetto(cls, valori: list[str]) -> list[str]:
        codici = _codici(valori, _TIPI_SOGGETTO, "Tipo di soggetto")
        for codice in codici:
            if codice in TIPI_SOGGETTO_DA_REGISTRO:
                raise BadRequestError(
                    "Dimensione, startup o PMI innovativa, impresa artigiana e cooperativa "
                    "si ricavano dal Registro Imprese: non si possono dichiarare"
                )
        return _al_massimo(codici, MAX_TIPI_SOGGETTO, "tipi di soggetto")

    @field_validator("ruoli_disponibili")
    @classmethod
    def _ruoli(cls, valori: list[str]) -> list[str]:
        codici = _codici(valori, _RUOLI, "Ruolo")
        if not codici:
            raise BadRequestError("Indica almeno un ruolo: capofila o partner")
        return codici

    @field_validator("settori_interesse")
    @classmethod
    def _settori(cls, valori: list[int]) -> list[int]:
        return _al_massimo(_id_lookup(valori, "Settore"), MAX_SETTORI, "settori")

    @field_validator("regioni_interesse")
    @classmethod
    def _regioni(cls, valori: list[int]) -> list[int]:
        return _al_massimo(_id_lookup(valori, "Regione"), MAX_REGIONI, "regioni")

    @field_validator("categorie_bando_escluse")
    @classmethod
    def _categorie(cls, valori: list[int]) -> list[int]:
        return _al_massimo(
            _id_lookup(valori, "Categoria di bando"), MAX_CATEGORIE_ESCLUSE, "categorie di bando"
        )

    @field_validator("paesi_interesse")
    @classmethod
    def _paesi(cls, valori: list[str]) -> list[str]:
        out: list[str] = []
        for valore in valori:
            codice = valore.strip().upper()
            codice = _ALIAS_PAESI.get(codice, codice)
            if codice not in PAESI_ISO2:
                raise BadRequestError(f"Codice paese non valido: «{valore[:10]}»")
            out.append(codice)
        return _al_massimo(_dedup(out), MAX_PAESI, "paesi")

    @field_validator("forme_accettate")
    @classmethod
    def _forme(cls, valori: list[str]) -> list[str]:
        return _codici(valori, FORME_PROFILO, "Forma di aggregazione")

    @field_validator("esperienze")
    @classmethod
    def _esperienze(cls, valori: list[EsperienzaPartner]) -> list[EsperienzaPartner]:
        return _al_massimo(valori, MAX_ESPERIENZE, "esperienze")

    @field_validator("certificazioni")
    @classmethod
    def _certificazioni(cls, valori: list[str]) -> list[str]:
        voci = _voci_brevi(valori, MAX_CERTIFICAZIONE, "certificazioni")
        return _al_massimo(voci, MAX_CERTIFICAZIONI, "certificazioni")


# Il client NON può inviare le origini 'admin' e 'sistema': quelle le scrivono
# solo l'area admin (WP9) e i trigger, altrimenti il registro sarebbe falsificabile.
OrigineConsensoClient = Literal["import_piva", "pagina_azienda", "wizard_call"]


class ConsensoIn(BaseModel):
    """Concessione, revoca o cambio di anonimato. `anonimo` è obbligatorio per
    `concedi` e `anonimato`, ma lo verifica la RPC (400 `anonimato_obbligatorio`,
    codice su cui il frontend ramifica), non lo schema. Booleano STRETTO: una
    scelta di consenso non si ricava da «yes» o da 1."""

    model_config = ConfigDict(extra="forbid")

    azione: Literal["concedi", "revoca", "anonimato"]
    informativa_versione: str = Field(min_length=1, max_length=50)
    origine: OrigineConsensoClient
    anonimo: StrictBool | None = None


class ReferenteIn(BaseModel):
    """Il titolare propone un membro come referente, annulla la proposta
    pendente (il referente in carica resta) o rimuove il referente."""

    model_config = ConfigDict(extra="forbid")

    azione: Literal["proponi", "annulla_proposta", "rimuovi"]
    user_id: UUID | None = None

    @model_validator(mode="after")
    def _serve_la_persona(self) -> "ReferenteIn":
        if self.azione == "proponi" and self.user_id is None:
            raise BadRequestError("Scegli la persona da proporre come referente")
        return self


class ReferenteRispostaIn(BaseModel):
    """Risposta del membro proposto (accetta/rifiuta) o rinuncia del referente."""

    model_config = ConfigDict(extra="forbid")

    azione: Literal["accetta", "rifiuta", "revoca"]
    informativa_versione: str | None = Field(default=None, max_length=50)

    @model_validator(mode="after")
    def _accettazione_informata(self) -> "ReferenteRispostaIn":
        if self.azione == "accetta" and not (self.informativa_versione or "").strip():
            raise BadRequestError(
                "Per accettare devi leggere e confermare l'informativa per il referente"
            )
        return self


# ------------------------------------------------------ output del modello


class MotivazioneCompetenza(BaseModel):
    codice: str  # codice di Competenza
    motivo: str


class BozzaProfiloAi(BaseModel):
    """Schema di OUTPUT imposto al modello: tutti i campi obbligatori, codici
    delle competenze come stringhe (quelli fuori vocabolario li scarta
    `pulisci_bozza`, prima del salvataggio in `bozza_ai`). Diventa visibile
    solo dopo «Applica» e salvataggio."""

    descrizione_competenze: str
    competenze: list[str]  # codici di Competenza
    motivazioni: list[MotivazioneCompetenza]


class MotivazioneCompetenzaOut(BaseModel):
    codice: Competenza
    motivo: str


class BozzaProfiloAiOut(BaseModel):
    """La bozza salvata come la rilegge l'API (`BozzaAiOut.proposta`):
    tipizzata sul vocabolario, a differenza dello schema del modello. I codici
    usciti dal vocabolario dopo il salvataggio li toglie
    `partner_profile_service._bozza_out`."""

    descrizione_competenze: str
    competenze: list[Competenza]
    motivazioni: list[MotivazioneCompetenzaOut]


# ----------------------------------------------------- vista del titolare


class ConsensoStatoOut(BaseModel):
    versione: str
    at: datetime


MotivoIdentita = Literal["dati_non_importati", "piva_diversa", "impresa_non_attiva", "dati_sandbox"]


# `non_disponibile`: il profilo nominativo è spento in questa versione
# (partner_profile_service.NOMINATIVO_DISPONIBILE), qualunque sia il titolare.
MotivoNominativo = Literal["cf_non_verificato", "non_rappresentante", "non_disponibile"]


class IdentitaPartnerOut(BaseModel):
    verificata: bool
    motivo: MotivoIdentita | None = None
    denominazione_registro: str | None = None
    puo_essere_nominativo: bool = False
    motivo_nominativo: MotivoNominativo | None = None


class EsperienzaPartnerOut(BaseModel):
    """Esperienza come salvata (lettura: tipi larghi, i dati sono già passati
    dalla validazione di `EsperienzaPartner` al salvataggio)."""

    programma: str
    programma_id: int | None = None
    anno: int | None = None
    ruolo: str | None = None
    titolo: str | None = None
    esito: str | None = None


class ProfiloPartnerDati(BaseModel):
    descrizione_competenze: str | None = None
    competenze: list[str] = Field(default_factory=list)
    competenze_libere: list[str] = Field(default_factory=list)
    tipi_soggetto: list[str] = Field(default_factory=list)
    ruoli_disponibili: list[str] = Field(default_factory=lambda: ["partner"])
    settori_interesse: list[int] = Field(default_factory=list)
    regioni_interesse: list[int] = Field(default_factory=list)
    paesi_interesse: list[str] = Field(default_factory=list)
    forme_accettate: list[str] = Field(default_factory=list)
    esperienze: list[EsperienzaPartnerOut] = Field(default_factory=list)
    certificazioni: list[str] = Field(default_factory=list)
    infrastrutture: str | None = None
    accetta_inviti: bool = True
    categorie_bando_escluse: list[int] = Field(default_factory=list)


class ReferentePropostoOut(BaseModel):
    nome: str | None = None
    sei_tu: bool = False


class ReferenteOut(BaseModel):
    tipo: Literal["titolare", "membro"]
    nome: str | None = None
    sei_tu: bool = False
    proposto: ReferentePropostoOut | None = None


class ReferentePossibileOut(BaseModel):
    user_id: UUID
    nome: str


class BozzaAiOut(BaseModel):
    stato: Literal["in_corso", "pronta", "errore"]
    avviata_at: datetime | None = None
    pronta_at: datetime | None = None
    errore: str | None = None
    proposta: BozzaProfiloAiOut | None = None


class PartnerProfileOut(BaseModel):
    """GET/PUT /me/partner-profile. Un membro riceve `editable=false`,
    `referenti_possibili=[]` e nessun `user_id` di altri."""

    editable: bool
    esiste: bool
    visibile: bool
    anonimo: bool
    sospeso: bool
    consenso: ConsensoStatoOut | None = None
    informativa_versione_corrente: str
    riconsenso_suggerito: bool
    identita: IdentitaPartnerOut
    profilo: ProfiloPartnerDati
    tipi_soggetto_dedotti: list[str] = Field(default_factory=list)
    completezza: int
    avvisi_anonimato: list[str] = Field(default_factory=list)
    referente: ReferenteOut
    referenti_possibili: list[ReferentePossibileOut] = Field(default_factory=list)
    bozza_ai: BozzaAiOut | None = None
    vocabolario_versione: int
    aggiornato_at: datetime | None = None


# ------------------------------------------------- proiezione verso terzi


class _Pubblico(BaseModel):
    # Whitelist anche nella costruzione: un campo non previsto è un errore.
    model_config = ConfigDict(extra="forbid")


class AtecoSezioneOut(_Pubblico):
    lettera: str
    descrizione: str


class FascePubblicheOut(_Pubblico):
    """Codici delle fasce di `bilanci_indicatori.calcola_fasce`, mai importi.
    Per i profili anonimi solo `fatturato`."""

    fatturato: str | None = None
    patrimonio_netto: str | None = None
    dipendenti: str | None = None
    trend: str | None = None


class TipoSoggettoPubblicoOut(_Pubblico):
    codice: str
    etichetta: str
    fonte: Literal["registro", "dichiarato"]


class CompetenzaPubblicaOut(_Pubblico):
    codice: str
    etichetta: str
    area: str


class EsperienzaPubblicaOut(_Pubblico):
    programma: str
    anno: int | None = None
    ruolo: str | None = None
    titolo: str | None = None


class FormaPubblicaOut(_Pubblico):
    codice: str
    etichetta: str


class PartnerPubblicoOut(_Pubblico):
    """Ciò che le altre aziende vedono di un partner (e l'anteprima «come ti
    vedono»). Handle opaco `codice_pubblico`: mai `company_profile_id`,
    `family_parent_id`, P.IVA, CF, contatti, importi di bilancio, referente.
    Per gli anonimi (Q12) `denominazione`, `infrastrutture` e anno, ruolo e
    titolo delle esperienze sono null, le fasce solo `fatturato` e le
    certificazioni diventano categorie."""

    codice_pubblico: UUID
    anonimo: bool
    denominazione: str | None = None
    regione_sede: str | None = None
    regioni_interesse: list[str] = Field(default_factory=list)
    paesi_interesse: list[str] = Field(default_factory=list)
    ateco_sezione: AtecoSezioneOut | None = None
    classe_dimensionale: str | None = None
    fasce: FascePubblicheOut = Field(default_factory=FascePubblicheOut)
    tipi_soggetto: list[TipoSoggettoPubblicoOut] = Field(default_factory=list)
    competenze: list[CompetenzaPubblicaOut] = Field(default_factory=list)
    competenze_libere: list[str] = Field(default_factory=list)
    descrizione_competenze: str | None = None
    esperienze: list[EsperienzaPubblicaOut] = Field(default_factory=list)
    certificazioni: list[str] = Field(default_factory=list)
    infrastrutture: str | None = None
    ruoli_disponibili: list[RuoloPartenariato] = Field(default_factory=list)
    forme_accettate: list[FormaPubblicaOut] = Field(default_factory=list)
    completezza: int = 0
    accetta_inviti: bool = True


# ------------------------------------------------------------- informativa


class InformativaPartnerOut(BaseModel):
    """GET /partenariati/informativa."""

    versione: str
    testo: str
    referente_versione: str
    referente_testo: str
