"""Candidature spontanee e inviti delle call di partenariato (WP7,
docs/partenariati.md K1-K2, T3-T5, Q13, Q14, Q25).

Una sola tabella `partner_candidature` (`tipo`: candidatura | invito), stati
`inviata → accettata | rifiutata | ritirata | scaduta`: ogni transizione
passa da una RPC della 0039, che ricontrolla tutto sotto i lock (owner →
azienda → controparte → call → candidatura). Qui si valida PRIMA della RPC
(niente `parametri_non_validi` da input dell'utente: resterebbe un 502).

- Chi agisce (T4, Q14): solo il titolare dell'azienda attiva
  (`active.editable`); i membri con visibilità leggono. Un Advisor agisce
  sull'azienda attiva: le righe di un'altra sua azienda sono 404.
- `invia_candidatura` (Y → call di X): messaggio 50..2000 senza contatti né
  identificativi dell'azienda (`partenariato_anonimato.trova_rilievi`,
  anonima: la candidata è anonima fino all'accettazione) → 400
  `testo_non_conforme`; requisiti dichiarati tra quelli VISIBILI della call;
  valutazione del match in vista «terzi» (solo esiti e fasce) salvata nella
  riga; pseudonimo per call; quota del piano, opt-in, identità dal registro e
  stato della call li decide la RPC.
- `invita` (X → Y): Y indicata SOLO dallo pseudonimo per call, risolto dal
  server sull'indice (`risolvi_pseudonimo`) e accettato solo se Y è ancora
  tra i suggeriti della call (`suggeriti_per_call`, stesso limite per owner)
  e, dal ricontrollo live, visibile e disponibile agli inviti; altrimenti
  409 `partner_non_disponibile` (codice neutro unico). Mai
  `company_profile_id` in ingresso né in uscita verso terzi.
- `decidi` / `ritira`: decide X le candidature e Y gli inviti; ritira chi ha
  mandato. All'accettazione la RPC crea la conversazione e scrive l'audit;
  quello di rivelazione solo con la rivelazione SIMMETRICA (WP9, decisione di
  Michele): interruttore globale acceso ed ENTRAMBE le aziende con l'identità
  verificata dalla piattaforma (`p_rivela`, ricontrollato dalla RPC). Le viste
  successive mostrano l'identità solo se oggi lo sono ancora
  (`identita_se_rivelata`): una verifica revocata la spegne, gli audit restano.
- Letture (`lista`, `dettaglio`) per lato: X vede Y solo per pseudonimo, con
  profilo pubblico (nel dettaglio) e valutazione solo finché Y è ancora
  visibile («Azienda non più disponibile» altrimenti, senza fasce); Y vede la
  call senza identità del creatore. Scadenza pigra degli inviti in lettura.
  Le fasce oltre al fatturato (WP9) solo per una Y che OGGI si mostra col nome
  (profilo nominativo e identità verificata dalla piattaforma): la valutazione
  si salva così (`match_per_terzi`) e si riduce in lettura se nel frattempo
  la verifica è stata revocata.
- Anti-abuso (fail-open, non è un tetto di spesa): candidature e inviti al
  giorno per utente.
- Notifiche in-app (canale affidabile) e email di evento in background, solo
  agli utenti con `eventi_abilitati` e recapitabili (`filtra_recapitabili`):
  mai dati di terzi oltre al titolo del bando e allo pseudonimo, mai testi.

Dopo ogni scrittura l'indice del matching si invalida (inviti per le call
solo su invito, esposizioni, impegni sul bando).

Log: solo id e codici; mai testi, P.IVA, email o nomi.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import replace
from datetime import date, datetime, timezone
from typing import Any, Literal, NoReturn
from uuid import UUID
from zoneinfo import ZoneInfo

from postgrest.exceptions import APIError
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import get_settings
from app.core.errors import AppError, BadRequestError, ForbiddenError, NotFoundError, UpstreamError
from app.schemas.common import Page
from app.schemas.partner_call import BandoPubblicoCallOut
from app.services import (
    bandi_service,
    bando_alert_service,
    email_service,
    lookup_service,
    partenariato_indice,
    partenariato_notifiche,
    rate_limit_service,
)
from app.services import partenariato_matching as pm
from app.services import partner_call_service as pcs
from app.services import partner_profile_service as pps
from app.services.notification_service import notify
from app.services.paginazione import pagina
from app.services.partenariato_accesso import (
    RUOLI_AZIENDA,
    RUOLI_SCRITTURA,
    IdentitaRivelataOut,
    ProfiloSuggeritoOut,
    StatoCandidatura,
    carica_call_autorizzata,
    identita_rivelata,
    pseudonimo,
    requisito_visibile,
    stato_effettivo,
    testo_pubblico,
)
from app.services.partenariato_anonimato import (
    ETICHETTE_RILIEVO,
    Identificativi,
    identificativi_azienda,
    senza_invisibili,
    trova_rilievi,
)
from app.services.partenariato_errori import RPC_ERRORS, raise_from_rpc
from app.services.partenariato_matching import FasceMatchOut, MatchOut
from app.services.partner_profilo_pubblico import profilo_pubblico

logger = logging.getLogger("bandofit.partenariati")

MESSAGGIO_CANDIDATURA_MIN, MESSAGGIO_CANDIDATURA_MAX = 50, 2000
MESSAGGIO_INVITO_MAX = 1000
MOTIVO_RIFIUTO_MAX = 500
MAX_REQUISITI_DICHIARATI = 40
FINESTRA_GIORNO_SECONDI = 86_400
# Inviti scaduti marcati a ogni lettura (scadenza pigra; il resto lo fa lo
# scheduler) e per giro dello scheduler.
SCADENZA_PIGRA_LOTTO = 50
SCADENZA_LOTTO = 500
SCADENZA_GIRI_MAX = 20

TIPO_INVITO_RICEVUTO = "partenariato.invito_ricevuto"
TIPO_CANDIDATURA_RICEVUTA = "partenariato.candidatura_ricevuta"
TIPO_ACCETTATA = "partenariato.candidatura_accettata"
TIPO_RIFIUTATA = "partenariato.candidatura_rifiutata"
AUDIT_IDENTITA = "partenariato.identita_rivelata"

MSG_SOLO_TITOLARE = "Candidature e inviti li gestisce il titolare dell'azienda"
MSG_AZIENDA_MANCANTE = "Importa o crea prima la tua azienda"
MSG_NON_TROVATA = "Candidatura non trovata"
MSG_LIMITE_CANDIDATURE = "Hai inviato molte candidature oggi: riprova domani"
MSG_LIMITE_INVITI = "Hai inviato molti inviti oggi: riprova domani"
MSG_REQUISITI = "Puoi dichiarare solo i requisiti che la call mostra"

# I detail comuni con il profilo partner e le call hanno lì un messaggio sul
# profilo o sulla call: qui si parla di candidature. Il code resta lo stesso.
_ERRORI_CANDIDATURE: dict[str, tuple[int, str, str]] = {
    "attore_non_titolare": (403, "forbidden", MSG_SOLO_TITOLARE),
    "identita_non_verificata": (
        409,
        "identita_non_verificata",
        "Per candidarti o accettare importa prima i dati ufficiali dell'azienda dalla partita "
        "IVA: l'impresa deve risultare attiva nel Registro Imprese",
    ),
    "requisiti_non_validi": (400, "bad_request", MSG_REQUISITI),
    "call_not_found": (404, "not_found", "Call di partenariato non trovata"),
}

CANDIDATURA_SELECT = (
    "id,partner_call_id,tipo,company_profile_id,creatore_company_profile_id,posizione_id,"
    "messaggio,requisiti_dichiarati,valutazione,pseudonimo,stato,motivo_chiusura,"
    "motivo_rifiuto,scade_at,conversazione_id,decisa_at,chiusa_at,created_at"
)
CALL_RIF_SELECT = (
    "id,bando_slug,bando_titolo,bando_scadenza,bando_stato_effettivo,titolo,stato,"
    "scadenza_call,visibilita"
)

Direzione = Literal["inviate", "ricevute"]
Lato = Literal["creatore", "partner"]
Decisione = Literal["accetta", "rifiuta"]

# Riferimenti ai task in background (email): senza, il garbage collector può
# cancellarli a metà.
_background_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    """Email in background (sostituibile nei test)."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


# ------------------------------------------------------------------ input


def pulisci_testo(valore: Any, cosa: str, *, massimo: int, minimo: int = 0) -> str | None:
    """Testo dell'utente senza caratteri invisibili (`senza_invisibili`), spazi
    ai bordi tolti, vuoto → None; lunghezze con messaggio (400)."""
    if valore is None:
        return None
    if not isinstance(valore, str):
        raise BadRequestError(f"{cosa} non è valido")
    pulito = senza_invisibili(valore).strip()
    if not pulito:
        if minimo:
            raise BadRequestError(f"{cosa} deve avere almeno {minimo} caratteri")
        return None
    if len(pulito) < minimo:
        raise BadRequestError(f"{cosa} deve avere almeno {minimo} caratteri")
    if len(pulito) > massimo:
        cifre = f"{massimo:,}".replace(",", ".")
        raise BadRequestError(f"{cosa} può avere al massimo {cifre} caratteri")
    return pulito


class CandidaturaIn(BaseModel):
    """POST /partenariati/call/{id}/candidature."""

    model_config = ConfigDict(extra="forbid")

    posizione_id: UUID
    messaggio: str
    requisiti_dichiarati: list[UUID] = Field(default_factory=list)

    @field_validator("messaggio", mode="before")
    @classmethod
    def _messaggio(cls, valore: Any) -> str:
        return pulisci_testo(valore, "Il messaggio", minimo=MESSAGGIO_CANDIDATURA_MIN,
                             massimo=MESSAGGIO_CANDIDATURA_MAX)

    @field_validator("requisiti_dichiarati")
    @classmethod
    def _requisiti(cls, valore: list[UUID]) -> list[UUID]:
        unici = list(dict.fromkeys(valore))
        if len(unici) > MAX_REQUISITI_DICHIARATI:
            raise BadRequestError(
                f"Puoi dichiarare al massimo {MAX_REQUISITI_DICHIARATI} requisiti")
        return unici


class InvitoIn(BaseModel):
    """POST /partenariati/call/{id}/inviti: l'azienda si indica SOLO con lo
    pseudonimo della call (mai `company_profile_id`)."""

    model_config = ConfigDict(extra="forbid")

    pseudonimo: str = Field(max_length=64)
    posizione_id: UUID | None = None
    messaggio: str | None = None

    @field_validator("messaggio", mode="before")
    @classmethod
    def _messaggio(cls, valore: Any) -> str | None:
        return pulisci_testo(valore, "Il messaggio dell'invito", massimo=MESSAGGIO_INVITO_MAX)


class RifiutoIn(BaseModel):
    """POST /partenariati/candidature/{id}/rifiuta: motivo facoltativo."""

    model_config = ConfigDict(extra="forbid")

    motivo: str | None = None

    @field_validator("motivo", mode="before")
    @classmethod
    def _motivo(cls, valore: Any) -> str | None:
        return pulisci_testo(valore, "Il motivo", massimo=MOTIVO_RIFIUTO_MAX)


# ------------------------------------------------------------------ output


class _Uscita(BaseModel):
    # Whitelist anche nella costruzione, come le proiezioni del WP5-WP6.
    model_config = ConfigDict(extra="forbid")


class CallRiferimentoOut(_Uscita):
    """La call a cui si riferisce una candidatura: solo dati pubblici (titolo
    ripulito, bando del catalogo), mai il creatore."""

    id: UUID
    titolo: str | None = None
    bando: BandoPubblicoCallOut
    stato: str
    scadenza_call: date | None = None
    visibilita: Literal["pubblica", "solo_invitati"] = "pubblica"


class PosizioneRiferimentoOut(_Uscita):
    id: UUID
    titolo: str


class RequisitoDichiaratoOut(_Uscita):
    requisito_id: UUID
    etichetta: str


class CandidatoOut(_Uscita):
    """L'azienda candidata o invitata vista dal creatore: lo pseudonimo della
    call (mai id interni né `codice_pubblico`) e, nel dettaglio, il profilo
    pubblico del WP4 finché è ancora visibile."""

    pseudonimo: str
    disponibile: bool
    profilo: ProfiloSuggeritoOut | None = None  # type: ignore[valid-type]


class QuotaCandidatureOut(_Uscita):
    """Candidature del mese del pool dell'owner (NULL = illimitate)."""

    usate: int
    limite: int | None = None


class InvitiCallOut(_Uscita):
    attivi: int
    massimo: int


class CandidaturaOut(_Uscita):
    """Una candidatura o un invito come lo vede l'azienda attiva (`lato`:
    `creatore` = ha creato la call, `partner` = si è candidata o è invitata).
    Dal lato del creatore l'altra azienda è solo `candidato` (pseudonimo) con
    la `valutazione` salvata all'invio (vista «terzi»: esiti e fasce) finché
    è disponibile; `compatibile` false = non risultava compatibile con i
    filtri della call (senza il motivo). Dal lato partner nessun dato del
    creatore. Mai `company_profile_id`, owner o utenti."""

    id: UUID
    tipo: Literal["candidatura", "invito"]
    lato: Lato
    stato: StatoCandidatura
    motivo_chiusura: str | None = None
    call: CallRiferimentoOut
    posizione: PosizioneRiferimentoOut | None = None
    messaggio: str | None = None
    requisiti_dichiarati: list[RequisitoDichiaratoOut] = Field(default_factory=list)
    candidato: CandidatoOut | None = None
    valutazione: MatchOut | None = None
    compatibile: bool | None = None
    motivo_rifiuto: str | None = None
    scade_at: datetime | None = None
    decisa_at: datetime | None = None
    chiusa_at: datetime | None = None
    created_at: datetime | None = None
    conversazione_id: UUID | None = None
    puo_decidere: bool = False
    puo_ritirare: bool = False
    quota: QuotaCandidatureOut | None = None
    inviti: InvitiCallOut | None = None


# ------------------------------------------------------------------ utilità


def _adesso() -> datetime:
    return datetime.now(timezone.utc)


def _oggi() -> date:
    return bandi_service.today_italy()


def _pesi() -> pm.PesiMatching:
    return pm.PesiMatching.da_settings(get_settings())


async def _una(query) -> dict | None:
    resp = await query.limit(1).execute()
    return resp.data[0] if resp.data and isinstance(resp.data[0], dict) else None


def _richiedi_titolare(active) -> None:
    if not active.editable:
        raise ForbiddenError(MSG_SOLO_TITOLARE)


def _richiedi_azienda(active) -> str:
    if not active.company_id:
        raise AppError(409, "azienda_mancante", MSG_AZIENDA_MANCANTE)
    return str(active.company_id)


def errore_mappa(detail: str) -> AppError:
    return AppError(*(_ERRORI_CANDIDATURE.get(detail) or RPC_ERRORS[detail]))


def _errore_rpc(exc: APIError) -> NoReturn:
    """Messaggi delle candidature per i detail comuni, poi la mappa unica."""
    detail = (exc.details or "").strip()
    if detail in _ERRORI_CANDIDATURE:
        raise AppError(*_ERRORI_CANDIDATURE[detail]) from exc
    raise_from_rpc(exc)


async def _rpc(primary, nome: str, parametri: dict) -> Any:
    try:
        resp = await primary.rpc(nome, parametri).execute()
    except APIError as exc:
        _errore_rpc(exc)
    return resp.data


def _normalizza(valore: Any) -> str:
    """Uuid in forma canonica; malformato = candidatura inesistente (404)."""
    try:
        return str(UUID(str(valore).strip()))
    except (ValueError, AttributeError, TypeError):
        raise NotFoundError(MSG_NON_TROVATA) from None


async def limite_anti_abuso(primary, tipo: str, user: Mapping, limite: int, finestra: int,
                            codice: str, messaggio: str) -> None:
    """Rate limit per utente (`rate_limit_service`, fail-open): anti-abuso,
    non è un tetto di spesa né la quota del piano."""
    chiave = rate_limit_service.bucket(tipo, str(user["id"]))
    if not await rate_limit_service.allow(primary, chiave, limite, finestra):
        raise AppError(429, codice, messaggio)


def controlla_testo(testo: str | None, ident: Identificativi | None, nome: str) -> None:
    """Primo rilievo bloccante (contatti, link, identificativi dell'azienda
    anonima) → 400 `testo_non_conforme`: nomina il tipo, MAI il dato."""
    if not testo:
        return
    for rilievo in trova_rilievi(testo, ident, anonima=True):
        if rilievo.bloccante:
            raise AppError(
                400,
                "testo_non_conforme",
                f"{nome} contiene {ETICHETTE_RILIEVO.get(rilievo.tipo, 'un dato non ammesso')}: "
                "toglilo e invia di nuovo. Prima dell'accettazione non si condividono contatti né "
                "dati che identificano l'azienda",
            )


def _lato(riga: Mapping, company_id: str) -> Lato | None:
    if str(riga.get("creatore_company_profile_id")) == company_id:
        return "creatore"
    if str(riga.get("company_profile_id")) == company_id:
        return "partner"
    return None


def _chi_decide(riga: Mapping) -> Lato:
    """Decide il destinatario: X le candidature, Y gli inviti."""
    return "creatore" if riga.get("tipo") == "candidatura" else "partner"


def _chi_ritira(riga: Mapping) -> Lato:
    """Ritira il mittente: Y le candidature, X gli inviti."""
    return "partner" if riga.get("tipo") == "candidatura" else "creatore"


def _uuid(valore: Any) -> UUID | None:
    try:
        return UUID(str(valore)) if valore else None
    except (ValueError, TypeError):
        return None


def _solo_fatturato(m: MatchOut) -> MatchOut:
    """Il match di un'azienda che oggi non si mostra col nome (Q12: agli
    anonimi la sola fascia di fatturato), anche se la valutazione era stata
    salvata quando si mostrava (WP9: una verifica revocata spegne le fasce
    in più nelle viste successive)."""
    if m.fasce is None:
        return m
    fasce = FasceMatchOut(fatturato=m.fasce.fatturato) if m.fasce.fatturato else None
    return m.model_copy(update={"fasce": fasce})


async def match_per_terzi(primary, m: pm.MatchInterno) -> pm.MatchInterno:
    """Il match come lo vedono i terzi (il creatore della call): un profilo
    salvato come nominativo conta come anonimo (la sola fascia di fatturato)
    se OGGI l'azienda non si mostra col nome, per l'interruttore spento o
    senza l'identità verificata dalla piattaforma (stessa regola di
    `pps.profilo_per_terzi`; lettura non riuscita → anonimo)."""
    if m.anonimo:
        return m
    if pps.NOMINATIVO_DISPONIBILE and await pps.identita_forte_o_no(primary, m.company_id):
        return m
    return replace(m, anonimo=True)


async def _nominativi_oggi(primary, company_ids: Iterable[str]) -> set[str]:
    """Le aziende, tra quelle date, che OGGI si mostrano col nome ai terzi
    (profilo salvato nominativo, interruttore acceso, identità verificata
    dalla piattaforma): solo per loro la valutazione salvata esce con tutte
    le fasce. L'identità si legge solo per i profili nominativi."""
    ids = sorted({str(c) for c in company_ids if c})
    if not ids or not pps.NOMINATIVO_DISPONIBILE:
        return set()
    nominativi: list[str] = []
    for blocco in _blocchi(ids):
        resp = await (
            primary.table("company_partner_profiles").select("company_profile_id")
            .in_("company_profile_id", blocco).eq("anonimo", False).execute()
        )
        nominativi += [str(r["company_profile_id"]) for r in resp.data or []
                       if isinstance(r, dict)]
    return await pps.aziende_con_identita_forte(primary, nominativi) if nominativi else set()


def _match(valutazione: Any) -> tuple[MatchOut | None, bool | None]:
    """(match in vista terzi, compatibile) dalla valutazione salvata."""
    if not isinstance(valutazione, dict) or not valutazione:
        return None, None
    if valutazione.get("compatibile") is False:
        return None, False
    try:
        return MatchOut.model_validate(valutazione), True
    except ValueError:
        logger.warning("candidature: valutazione salvata non leggibile")
        return None, None


def call_riferimento(call: Mapping, ident: Identificativi | None = None) -> CallRiferimentoOut:
    """`ident` = identificativi del creatore quando la guarda l'altra azienda
    (difesa in profondità, come la proiezione pubblica e la bacheca)."""
    return CallRiferimentoOut(
        id=call["id"],
        titolo=testo_pubblico(call.get("titolo"), ident),
        bando=BandoPubblicoCallOut(
            slug=str(call.get("bando_slug") or ""),
            titolo=str(call.get("bando_titolo") or ""),
            scadenza=call.get("bando_scadenza"),
            stato_effettivo=call.get("bando_stato_effettivo"),
        ),
        stato=str(call.get("stato") or ""),
        scadenza_call=call.get("scadenza_call"),
        visibilita="solo_invitati" if call.get("visibilita") == "solo_invitati" else "pubblica",
    )


def _data_italiana(valore: Any) -> str | None:
    """Scadenza per le email: «gg/mm/aaaa» nel fuso degli alert."""
    if not valore:
        return None
    try:
        istante = datetime.fromisoformat(str(valore).replace("Z", "+00:00"))
    except ValueError:
        return None
    if istante.tzinfo is None:
        istante = istante.replace(tzinfo=timezone.utc)
    return istante.astimezone(ZoneInfo(get_settings().alert_fuso)).strftime("%d/%m/%Y")


# ------------------------------------------------------------ proiezioni


class _Contesto:
    """Letture a blocco per proiettare una pagina di righe. `ident` =
    identificativi dei creatori delle call su cui l'azienda attiva è il
    partner (per ripulire i loro testi)."""

    def __init__(self) -> None:
        self.calls: dict[str, dict] = {}
        self.posizioni: dict[str, dict] = {}
        self.requisiti: dict[str, dict] = {}
        self.disponibili: set[str] = set()
        # Candidate che oggi si mostrano col nome (tutte le fasce): WP9.
        self.nominativi: set[str] = set()
        self.ident: dict[str, Identificativi] = {}


# Solo ciò che serve agli identificativi (`identificativi_azienda`): mai
# `raw` intero, nulla resta oltre la proiezione.
_IDENT_AZIENDA_SELECT = "id,ragione_sociale,partita_iva,codice_fiscale,sito_web"
_IDENT_DATI_SELECT = (
    "company_profile_id,denominazione,piva_fetched,r_piva:raw->companyDetails->>vatCode,"
    "r_cf:raw->companyDetails->>taxCode,r_sito:raw->webAndSocial->>website"
)


async def ident_creatori(primary, ids: Iterable[str]) -> dict[str, Identificativi]:
    """Identificativi delle aziende creatrici (ragione sociale, P.IVA, CF,
    sito e denominazione del registro), a blocchi: titolo della call, titoli
    delle posizioni, etichette dei requisiti e testi del creatore escono
    verso l'altra azienda senza di essi, come nella proiezione pubblica
    (anche se sono cambiati dopo il salvataggio)."""
    unici = sorted({str(i) for i in ids if i})
    aziende: dict[str, dict] = {}
    dati: dict[str, dict] = {}
    for blocco in _blocchi(unici):
        resp = await primary.table("company_profiles").select(_IDENT_AZIENDA_SELECT) \
            .in_("id", blocco).execute()
        aziende.update({str(r["id"]): r for r in resp.data or [] if isinstance(r, dict)})
        resp = await primary.table("company_data").select(_IDENT_DATI_SELECT) \
            .in_("company_profile_id", blocco).execute()
        dati.update({str(r["company_profile_id"]): r for r in resp.data or []
                     if isinstance(r, dict)})
    uscita: dict[str, Identificativi] = {}
    for cid, azienda in aziende.items():
        registro = dati.get(cid) or {}
        uscita[cid] = identificativi_azienda(
            azienda,
            {"denominazione": registro.get("denominazione"),
             "piva_fetched": registro.get("piva_fetched"),
             "raw": {"companyDetails": {"vatCode": registro.get("r_piva"),
                                        "taxCode": registro.get("r_cf")},
                     "webAndSocial": {"website": registro.get("r_sito")}}},
            None,
        )
    return uscita


async def _contesto(primary, righe: list[dict], company_id: str) -> _Contesto:
    ctx = _Contesto()
    if not righe:
        return ctx
    call_ids = sorted({str(r["partner_call_id"]) for r in righe})
    pos_ids = sorted({str(r["posizione_id"]) for r in righe if r.get("posizione_id")})
    req_ids = sorted({str(i) for r in righe for i in (r.get("requisiti_dichiarati") or [])})
    candidati = sorted({str(r["company_profile_id"]) for r in righe
                        if _lato(r, company_id) == "creatore"})
    for blocco in _blocchi(call_ids):
        resp = await primary.table("partner_calls").select(CALL_RIF_SELECT).in_("id", blocco) \
            .execute()
        ctx.calls.update({str(c["id"]): c for c in resp.data or []})
    for blocco in _blocchi(pos_ids):
        resp = await primary.table("partner_call_posizioni").select("id,call_id,titolo") \
            .in_("id", blocco).execute()
        ctx.posizioni.update({str(p["id"]): p for p in resp.data or []})
    for blocco in _blocchi(req_ids):
        resp = await primary.table("partner_call_requisiti") \
            .select("id,call_id,etichetta,cercato,ambito").in_("id", blocco).execute()
        ctx.requisiti.update({str(q["id"]): q for q in resp.data or []})
    if candidati:
        ctx.disponibili = set(await partenariato_indice.ricontrollo_live(primary, candidati))
        ctx.nominativi = await _nominativi_oggi(primary, ctx.disponibili)
    creatori = [str(r["creatore_company_profile_id"]) for r in righe
                if _lato(r, company_id) == "partner"]
    if creatori:
        ctx.ident = await ident_creatori(primary, creatori)
    return ctx


def _blocchi(ids: list[str], dimensione: int = 100) -> Iterable[list[str]]:
    for inizio in range(0, len(ids), dimensione):
        yield ids[inizio : inizio + dimensione]


def proietta(
    riga: Mapping,
    ctx: _Contesto,
    *,
    company_id: str,
    editable: bool,
    profilo: Any = None,
    adesso: datetime | None = None,
) -> CandidaturaOut:
    """Una riga per il lato dell'azienda attiva (whitelist)."""
    lato = _lato(riga, company_id)
    if lato is None:
        raise NotFoundError(MSG_NON_TROVATA)
    call_id = str(riga["partner_call_id"])
    call = ctx.calls.get(call_id) or {"id": call_id}  # riga sparita: solo l'id
    stato = stato_effettivo(riga, adesso)
    in_attesa = stato == "inviata"
    creatore = lato == "creatore"
    disponibile = str(riga.get("company_profile_id")) in ctx.disponibili
    # Dal lato partner i testi del creatore escono senza i suoi identificativi.
    ident = None if creatore else ctx.ident.get(str(riga.get("creatore_company_profile_id")))
    # Il testo dell'ALTRA azienda (candidata per il creatore, creatore per
    # l'invitata) esce ripulito dai contatti (difesa in profondità: è già
    # controllato all'invio); dal creatore solo finché la candidata è
    # disponibile.
    proprio = (riga.get("tipo") == "candidatura") != creatore
    messaggio = riga.get("messaggio")
    if not proprio:
        messaggio = testo_pubblico(messaggio, ident) if (disponibile or not creatore) else None
    posizione = ctx.posizioni.get(str(riga.get("posizione_id")))
    requisiti = []
    if not creatore or disponibile:
        for rid in riga.get("requisiti_dichiarati") or []:
            voce = ctx.requisiti.get(str(rid))
            # Solo requisiti ancora della call e visibili ai terzi.
            if voce and str(voce.get("call_id")) == call_id and requisito_visibile(voce):
                requisiti.append(RequisitoDichiaratoOut(
                    requisito_id=voce["id"],
                    etichetta=testo_pubblico(voce.get("etichetta"), ident) or "?",
                ))
    valutazione, compatibile = _match(riga.get("valutazione")) if creatore and disponibile \
        else (None, None)
    if valutazione is not None and str(riga.get("company_profile_id")) not in ctx.nominativi:
        valutazione = _solo_fatturato(valutazione)
    return CandidaturaOut(
        id=riga["id"],
        tipo=riga["tipo"],
        lato=lato,
        stato=stato,
        motivo_chiusura=riga.get("motivo_chiusura") or ("ttl" if stato != riga.get("stato")
                                                         else None),
        call=call_riferimento(call, ident),
        posizione=PosizioneRiferimentoOut(
            id=posizione["id"],
            titolo=testo_pubblico(posizione.get("titolo"), ident) or "Posizione",
        ) if posizione and str(posizione.get("call_id")) == call_id else None,
        messaggio=messaggio,
        requisiti_dichiarati=requisiti,
        candidato=CandidatoOut(
            pseudonimo=str(riga.get("pseudonimo") or ""),
            disponibile=disponibile,
            profilo=profilo if disponibile else None,
        ) if creatore else None,
        valutazione=valutazione,
        compatibile=compatibile,
        motivo_rifiuto=testo_pubblico(riga.get("motivo_rifiuto"), ident),
        scade_at=riga.get("scade_at"),
        decisa_at=riga.get("decisa_at"),
        chiusa_at=riga.get("chiusa_at"),
        created_at=riga.get("created_at"),
        conversazione_id=_uuid(riga.get("conversazione_id")),
        puo_decidere=bool(editable and in_attesa and _chi_decide(riga) == lato),
        puo_ritirare=bool(editable and in_attesa and _chi_ritira(riga) == lato),
    )


async def _profilo_candidato(primary, secondary, company_id: str):
    """Profilo pubblico del WP4 della candidata (whitelist, Q12 per gli
    anonimi) SENZA `codice_pubblico`: solo nel dettaglio, per il creatore.
    Nominativo solo con l'identità verificata oggi (WP9)."""
    resp = (
        await primary.table("company_partner_profiles").select(pcs.PROFILO_PUBBLICO_SELECT)
        .eq("company_profile_id", company_id).eq("visibile_come_partner", True)
        .is_("sospeso_at", "null").limit(1).execute()
    )
    riga = resp.data[0] if resp.data else None
    if riga is None:
        return None
    if riga.get("anonimo") is False:
        riga = pps.profilo_per_terzi(riga, await pps.identita_forte_o_no(primary, company_id))
    try:
        az = await pcs.carica_azienda(primary, company_id, None)
    except NotFoundError:
        return None
    # Le fasce dal profilo di matching (gli stessi bilanci dei suggeriti).
    try:
        idx = await partenariato_indice.indice(primary, secondary)
        matching = await partenariato_indice.profilo_azienda(primary, idx, company_id)
        fasce = matching.fasce if matching is not None else None
    except Exception as exc:  # noqa: BLE001 — senza fasce il profilo resta valido
        logger.warning("candidature: fasce della candidata non lette (%s)",
                       getattr(exc, "code", None) or type(exc).__name__)
        fasce = None
    try:
        lookups = await lookup_service.get_lookups(secondary)
    except Exception as exc:  # noqa: BLE001 — servono solo ai nomi
        logger.warning("candidature: lookup del catalogo non disponibili (%s)",
                       type(exc).__name__)
        lookups = None
    pubblico = profilo_pubblico(riga, az.dati_registro, az.dossier, fasce, lookups,
                                ident=az.ident)
    return ProfiloSuggeritoOut(**pubblico.model_dump(exclude={"codice_pubblico"}))


async def _riga(primary, candidatura_id: Any, company_id: str) -> dict:
    """La riga se l'azienda attiva ne è una delle due parti (404 altrimenti,
    anche per un'altra azienda dello stesso owner)."""
    identificativo = _normalizza(candidatura_id)
    resp = (
        await primary.table("partner_candidature").select(CANDIDATURA_SELECT)
        .eq("id", identificativo).limit(1).execute()
    )
    riga = resp.data[0] if resp.data else None
    if not isinstance(riga, dict) or _lato(riga, company_id) is None:
        raise NotFoundError(MSG_NON_TROVATA)
    return riga


async def _una_proiettata(primary, secondary, active, riga: Mapping, *,
                          con_profilo: bool = False) -> CandidaturaOut:
    company_id = str(active.company_id)
    ctx = await _contesto(primary, [dict(riga)], company_id)
    profilo = None
    candidata = str(riga.get("company_profile_id"))
    if con_profilo and _lato(riga, company_id) == "creatore" and candidata in ctx.disponibili:
        profilo = await _profilo_candidato(primary, secondary, candidata)
    return proietta(riga, ctx, company_id=company_id, editable=bool(active.editable),
                    profilo=profilo)


# ------------------------------------------------------------- notifiche


async def _impostazioni_eventi(primary, user_ids: list[str]) -> dict[str, dict]:
    """Righe di `partner_email_settings` (create al primo uso: il token di
    disiscrizione nasce con la riga)."""
    if not user_ids:
        return {}
    await primary.table("partner_email_settings").upsert(
        [{"user_id": uid} for uid in user_ids], on_conflict="user_id", ignore_duplicates=True
    ).execute()
    righe: dict[str, dict] = {}
    for blocco in _blocchi(sorted(user_ids)):
        resp = (
            await primary.table("partner_email_settings")
            .select("user_id,eventi_abilitati,unsubscribe_token")
            .in_("user_id", blocco)
            .execute()
        )
        righe.update({str(r["user_id"]): r for r in resp.data or []})
    return righe


InviaEmail = Callable[[str, Any, str | None], Awaitable[bool]]


async def email_evento(primary, company_id: str, destinatari: list[dict], invia: InviaEmail
                       ) -> int:
    """Email di evento agli utenti dell'azienda destinataria: SOLO recapitabili
    (email verificata, non soppressa) e con `eventi_abilitati`, ciascuno
    isolato. Non solleva mai. → email partite."""
    try:
        recapitabili = await bando_alert_service.filtra_recapitabili(primary, destinatari)
        if not recapitabili:
            return 0
        impostazioni = await _impostazioni_eventi(primary, [str(d["id"]) for d in recapitabili])
        azienda = await _una(
            primary.table("company_profiles").select("id,ragione_sociale").eq("id", company_id)
        )
        nome = ((azienda or {}).get("ragione_sociale") or "").strip() or None
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("partenariati: email di evento non preparate (azienda %s, %s)",
                       company_id, getattr(exc, "code", None) or type(exc).__name__)
        return 0
    inviate = 0
    for destinatario in recapitabili:
        riga = impostazioni.get(str(destinatario["id"]))
        if not riga or riga.get("eventi_abilitati") is False:
            continue
        try:
            if await invia(destinatario["email"], riga.get("unsubscribe_token"), nome):
                inviate += 1
        except Exception as exc:  # noqa: BLE001 — destinatario isolato
            logger.warning("partenariati: email di evento non inviata (utente %s, %s)",
                           destinatario.get("id"), type(exc).__name__)
    return inviate


async def notifica_evento(
    primary,
    *,
    company_id: str,
    tipo: str,
    titolo: str,
    corpo: str | None,
    url: str,
    dedup_key: str,
    email: InviaEmail | None = None,
    destinatari: list[dict] | None = None,
) -> None:
    """Notifica in-app agli utenti dell'azienda (titolare e membri attivi
    con visibilità, o `destinatari`) con deep link `?azienda=` già nell'url,
    poi, in background, l'email di evento. Best-effort: non solleva mai (la
    transizione è già avvenuta)."""
    try:
        lista = destinatari if destinatari is not None else (
            await partenariato_notifiche.destinatari_azienda(primary, company_id)
        )
        if not lista:
            return
        await notify(primary, [str(d["id"]) for d in lista], tipo=tipo, titolo=titolo,
                     corpo=corpo, url=url, dedup_key=dedup_key, company_profile_id=company_id)
        if email is not None:
            _spawn(email_evento(primary, company_id, lista, email))
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("partenariati: notifica %s non recapitata (azienda %s, %s)", tipo,
                       company_id, getattr(exc, "code", None) or type(exc).__name__)


def url_assoluto(percorso: str) -> str:
    return f"{get_settings().frontend_url.rstrip('/')}{percorso}"


def _bando(call: Mapping) -> str:
    return str(call.get("bando_titolo") or "del catalogo")


# ------------------------------------------------------------- scritture


async def _requisiti_visibili(primary, call_id: str) -> set[str]:
    resp = (
        await primary.table("partner_call_requisiti").select("id,cercato,ambito")
        .eq("call_id", call_id).execute()
    )
    return {str(r["id"]) for r in resp.data or [] if requisito_visibile(r)}


async def _valutazione(primary, secondary, call_id: str, company_id: str) -> dict:
    """Match della candidata con la call in vista «terzi» (solo esiti e
    fasce, mai valori né punteggio). `{"compatibile": false}` se un filtro
    rigido la esclude (il motivo resta interno); `{}` se non si può valutare
    (call fuori dall'indice, indice non leggibile): best-effort."""
    try:
        idx = await partenariato_indice.indice(primary, secondary)
        snapshot = idx.matching.calls.get(call_id)
        if snapshot is None:
            return {}
        profilo = await partenariato_indice.profilo_azienda(primary, idx, company_id)
        if profilo is None:
            return {}
        m = pm.valuta_coppia(snapshot, profilo, oggi=_oggi(), pesi=_pesi(),
                             direzione="suggeriti")
    except Exception as exc:  # noqa: BLE001 — la valutazione è informativa
        logger.warning("candidature: valutazione non calcolata (call %s, %s)", call_id,
                       getattr(exc, "code", None) or type(exc).__name__)
        return {}
    if m is None:
        return {"compatibile": False}
    return pm.proietta_match(await match_per_terzi(primary, m),
                             vista="terzi").model_dump(mode="json")


async def _codice_pubblico(primary, company_id: str) -> str:
    """Codice pubblico del profilo partner (per lo pseudonimo). Senza profilo
    si usa quello «assente»: la RPC rifiuta comunque, con il suo ordine di
    controlli (piano prima dell'opt-in)."""
    resp = (
        await primary.table("company_partner_profiles").select("codice_pubblico")
        .eq("company_profile_id", company_id).limit(1).execute()
    )
    codice = (resp.data[0] if resp.data else {}).get("codice_pubblico")
    return str(codice or pps.CODICE_PUBBLICO_ASSENTE)


async def invia_candidatura(primary, secondary, active, user: dict, call_id: Any,
                            dati: CandidaturaIn) -> CandidaturaOut:
    """Candidatura spontanea dell'azienda attiva a una call di altri.
    Errori: 403 (non titolare), 404 (call non visibile), 400
    (`testo_non_conforme`, requisiti non visibili, posizione), 409
    (`stesso_gruppo`, `call_non_attiva`, `call_solo_invitati`,
    `funzione_non_inclusa`, `profilo_partner_non_attivo`,
    `identita_non_verificata`, `candidature_esaurite`,
    `candidatura_gia_attiva`, `invito_gia_attivo`), 429
    `limite_candidature`."""
    _richiedi_titolare(active)
    company_id = _richiedi_azienda(active)
    call, ruolo = await carica_call_autorizzata(primary, call_id, active, user)
    if ruolo in RUOLI_AZIENDA:
        raise errore_mappa("stesso_gruppo")
    cid = str(call["id"])
    az = await pcs.carica_azienda(primary, company_id, active.owner_id)
    controlla_testo(dati.messaggio, az.ident, "Il messaggio")
    if not {str(r) for r in dati.requisiti_dichiarati} <= await _requisiti_visibili(primary, cid):
        raise BadRequestError(MSG_REQUISITI)
    settings = get_settings()
    await limite_anti_abuso(primary, "partner_candidatura", user,
                            settings.partner_candidature_limite_giorno, FINESTRA_GIORNO_SECONDI,
                            "limite_candidature", MSG_LIMITE_CANDIDATURE)
    valutazione = await _valutazione(primary, secondary, cid, company_id)
    handle = pseudonimo(cid, await _codice_pubblico(primary, company_id))
    esito = await _rpc(primary, "fn_partner_invia_candidatura", {"p_payload": {
        "owner_id": str(active.owner_id),
        "company_id": company_id,
        "attore_id": str(user["id"]),
        "call_id": cid,
        "posizione_id": str(dati.posizione_id),
        "messaggio": dati.messaggio,
        "requisiti_dichiarati": [str(r) for r in dati.requisiti_dichiarati],
        "valutazione": valutazione,
        "pseudonimo": handle,
        "richiedi_non_sandbox": pps.richiedi_non_sandbox(),
    }})
    riga = (esito or {}).get("candidatura") if isinstance(esito, dict) else None
    if not isinstance(riga, dict) or not riga.get("id"):
        raise UpstreamError()
    partenariato_indice.invalida()
    creatore = str(call["company_profile_id"])
    percorso = f"/app/partenariati/call/{cid}?tab=candidature&azienda={creatore}"
    await notifica_evento(
        primary,
        company_id=creatore,
        tipo=TIPO_CANDIDATURA_RICEVUTA,
        titolo="Hai ricevuto una candidatura",
        corpo=f"Un'azienda si è candidata alla tua call per il bando «{_bando(call)}».",
        url=percorso,
        dedup_key=f"partner-candidatura:{riga['id']}",
        email=lambda to, token, azienda: email_service.send_partner_candidatura_email(
            to, bando_titolo=call.get("bando_titolo"), pseudonimo=handle,
            cta_url=url_assoluto(percorso), unsubscribe_token=token,
            azienda_destinataria=azienda),
    )
    out = await _una_proiettata(primary, secondary, active, riga)
    limite = esito.get("limite")
    return out.model_copy(update={"quota": QuotaCandidatureOut(
        usate=int(esito.get("usate") or 0),
        limite=int(limite) if isinstance(limite, int) else None,
    )})


async def invita(primary, secondary, active, user: dict, call_id: Any,
                 dati: InvitoIn) -> CandidaturaOut:
    """Invito del creatore (titolare) a un'azienda suggerita, indicata dallo
    pseudonimo della call. Errori: 403, 404 (call non dell'azienda attiva),
    400 `testo_non_conforme`, 409 (`partner_non_disponibile` neutro,
    `call_non_attiva`, `invito_gia_attivo`, `candidatura_gia_attiva`,
    `inviti_esauriti_call`), 429 `limite_inviti`."""
    _richiedi_titolare(active)
    company_id = _richiedi_azienda(active)
    call, _ = await carica_call_autorizzata(primary, call_id, active, user,
                                            ammessi=RUOLI_SCRITTURA)
    cid = str(call["id"])
    if call.get("stato") != "pubblicata":
        raise errore_mappa("call_non_attiva")
    az = await pcs.carica_azienda(primary, company_id, active.owner_id)
    controlla_testo(dati.messaggio, az.ident, "Il messaggio dell'invito")
    settings = get_settings()
    await limite_anti_abuso(primary, "partner_invito", user, settings.partner_inviti_limite_giorno,
                            FINESTRA_GIORNO_SECONDI, "limite_inviti", MSG_LIMITE_INVITI)
    handle = dati.pseudonimo.strip()
    idx = await partenariato_indice.indice(primary, secondary)
    invitata = partenariato_indice.risolvi_pseudonimo(idx, cid, handle)
    m = next(
        (m for m in pm.suggeriti_per_call(idx.matching, cid, oggi=_oggi(), pesi=_pesi())
         if m.company_id == invitata),
        None,
    ) if invitata else None
    vivi = await partenariato_indice.ricontrollo_live(primary, [invitata]) if m else {}
    if m is None or not (vivi.get(invitata) or {}).get("accetta_inviti"):
        raise errore_mappa("partner_non_disponibile")
    esito = await _rpc(primary, "fn_partner_invita", {"p_payload": {
        "owner_id": str(active.owner_id),
        "company_id": company_id,
        "attore_id": str(user["id"]),
        "call_id": cid,
        "invitato_company_id": invitata,
        "posizione_id": str(dati.posizione_id) if dati.posizione_id else None,
        "messaggio": dati.messaggio,
        "valutazione": pm.proietta_match(await match_per_terzi(primary, m),
                                         vista="terzi").model_dump(mode="json"),
        "pseudonimo": handle,
        "max_inviti": settings.partner_inviti_max_per_call,
        "ttl_giorni": settings.partner_invito_ttl_giorni,
    }})
    riga = (esito or {}).get("candidatura") if isinstance(esito, dict) else None
    if not isinstance(riga, dict) or not riga.get("id"):
        raise UpstreamError()
    partenariato_indice.invalida()
    percorso = f"/app/partenariati/call/{cid}?azienda={invitata}"
    scadenza = _data_italiana(riga.get("scade_at"))
    await notifica_evento(
        primary,
        company_id=invitata,
        tipo=TIPO_INVITO_RICEVUTO,
        titolo="Hai ricevuto un invito a una call di partenariato",
        corpo=f"Un'azienda ti ha invitato alla sua call per il bando «{_bando(call)}»."
        + (f" L'invito scade il {scadenza}." if scadenza else ""),
        url=percorso,
        dedup_key=f"partner-invito:{riga['id']}",
        email=lambda to, token, azienda: email_service.send_partner_invito_email(
            to, bando_titolo=call.get("bando_titolo"), cta_url=url_assoluto(percorso),
            unsubscribe_token=token, azienda_destinataria=azienda, scadenza=scadenza),
    )
    out = await _una_proiettata(primary, secondary, active, riga)
    return out.model_copy(update={"inviti": InvitiCallOut(
        attivi=int(esito.get("inviti_attivi") or 0), massimo=int(esito.get("max_inviti") or 0),
    )})


async def decidi(primary, secondary, active, user: dict, candidatura_id: Any,
                 decisione: Decisione, dati: RifiutoIn | None = None) -> CandidaturaOut:
    """Accetta o rifiuta (titolare dell'azienda che decide: il creatore per le
    candidature, l'invitata per gli inviti; l'altra parte → 404).
    L'accettazione crea la conversazione nella stessa transazione della RPC,
    con l'audit (quello di rivelazione solo se ENTRAMBE le aziende hanno
    l'identità verificata dalla piattaforma: `pps.rivelazione_ammessa`, poi
    ricontrollata dalla RPC; una lettura non riuscita → 502 senza decisione).
    Errori: 403, 404, 400 `testo_non_conforme` (motivo), 409
    (`candidatura_gia_decisa`, `invito_scaduto`, `call_non_attiva`,
    `esclusivita_violata`, `profilo_partner_non_attivo`,
    `controparte_non_disponibile`, `identita_non_verificata`)."""
    _richiedi_titolare(active)
    company_id = _richiedi_azienda(active)
    riga = await _riga(primary, candidatura_id, company_id)
    if _lato(riga, company_id) != _chi_decide(riga):
        raise NotFoundError(MSG_NON_TROVATA)
    motivo = dati.motivo if dati is not None and decisione == "rifiuta" else None
    if motivo:
        az = await pcs.carica_azienda(primary, company_id, active.owner_id)
        controlla_testo(motivo, az.ident, "Il motivo")
    rivela = decisione == "accetta" and await pps.rivelazione_ammessa(
        primary, riga["company_profile_id"], riga["creatore_company_profile_id"]
    )
    esito = await _rpc(primary, "fn_partner_decidi", {
        "p_candidatura": str(riga["id"]),
        "p_attore": str(user["id"]),
        "p_owner": str(active.owner_id),
        "p_company": company_id,
        "p_decisione": decisione,
        "p_motivo": motivo,
        "p_rivela": bool(rivela),
        "p_richiedi_non_sandbox": pps.richiedi_non_sandbox(),
    })
    nuova = (esito or {}).get("candidatura") if isinstance(esito, dict) else None
    if not isinstance(nuova, dict) or not nuova.get("id"):
        raise UpstreamError()
    partenariato_indice.invalida()
    await _notifica_esito(primary, nuova, esito.get("conversazione_id"))
    return await _una_proiettata(primary, secondary, active, nuova)


async def _notifica_esito(primary, riga: Mapping, conversazione_id: Any) -> None:
    """All'altra parte: la candidata (esito di una candidatura) o il creatore
    (esito di un invito, con lo pseudonimo dell'invitata). Mai il motivo del
    rifiuto."""
    accettata = riga.get("stato") == "accettata"
    tipo = str(riga.get("tipo"))
    destinataria = str(riga["company_profile_id"] if tipo == "candidatura"
                       else riga["creatore_company_profile_id"])
    call = await _una(
        primary.table("partner_calls").select(CALL_RIF_SELECT)
        .eq("id", str(riga["partner_call_id"]))
    ) or {}
    cid = str(riga["partner_call_id"])
    # Chi riceve l'esito è chi aveva MANDATO la riga: la trova tra le inviate
    # (la candidatura nella vista «Candidature», l'invito nella scheda della
    # call), che non sono la sotto-lista di partenza.
    if accettata and conversazione_id:
        percorso = f"/app/partenariati/conversazioni/{conversazione_id}?azienda={destinataria}"
    elif tipo == "candidatura":
        percorso = (f"/app/partenariati?vista=candidature&direzione=inviate"
                    f"&azienda={destinataria}")
    else:
        percorso = (f"/app/partenariati/call/{cid}?tab=candidature&direzione=inviate"
                    f"&azienda={destinataria}")
    if tipo == "candidatura":
        titolo = ("La tua candidatura è stata accettata" if accettata
                  else "La tua candidatura non è stata accolta")
    else:
        titolo = ("Il tuo invito è stato accettato" if accettata
                  else "Il tuo invito è stato rifiutato")
    corpo = f"Call di partenariato per il bando «{_bando(call)}»." + (
        " Ora potete scrivervi nella chat della piattaforma." if accettata else "")
    pseudo = riga.get("pseudonimo") if tipo == "invito" else None
    await notifica_evento(
        primary,
        company_id=destinataria,
        tipo=TIPO_ACCETTATA if accettata else TIPO_RIFIUTATA,
        titolo=titolo,
        corpo=corpo,
        url=percorso,
        dedup_key=f"partner-esito:{riga['id']}",
        email=lambda to, token, azienda: email_service.send_partner_esito_email(
            to, esito="accettata" if accettata else "rifiutata", tipo=tipo,
            bando_titolo=call.get("bando_titolo"), cta_url=url_assoluto(percorso),
            unsubscribe_token=token, pseudonimo=pseudo, azienda_destinataria=azienda),
    )


async def ritira(primary, secondary, active, user: dict, candidatura_id: Any) -> CandidaturaOut:
    """Ritiro della propria candidatura (Y) o del proprio invito (X), solo in
    attesa. Errori: 403, 404, 409 (`candidatura_gia_decisa`,
    `invito_scaduto`)."""
    _richiedi_titolare(active)
    company_id = _richiedi_azienda(active)
    riga = await _riga(primary, candidatura_id, company_id)
    if _lato(riga, company_id) != _chi_ritira(riga):
        raise NotFoundError(MSG_NON_TROVATA)
    esito = await _rpc(primary, "fn_partner_ritira", {
        "p_candidatura": str(riga["id"]),
        "p_attore": str(user["id"]),
        "p_owner": str(active.owner_id),
        "p_company": company_id,
    })
    nuova = (esito or {}).get("candidatura") if isinstance(esito, dict) else None
    if not isinstance(nuova, dict) or not nuova.get("id"):
        raise UpstreamError()
    partenariato_indice.invalida()
    return await _una_proiettata(primary, secondary, active, nuova)


# ------------------------------------------------------------------ letture


async def scadi_inviti(primary, limite: int = SCADENZA_LOTTO) -> int:
    """Inviti in attesa oltre la scadenza → `scaduta` (`fn_partner_scadi_inviti`,
    salta le righe bloccate da una decisione in corso). Indice invalidato se
    ne ha chiusi."""
    resp = await primary.rpc("fn_partner_scadi_inviti", {"p_limite": limite}).execute()
    chiusi = int(resp.data or 0)
    if chiusi:
        partenariato_indice.invalida()
    return chiusi


async def _scadenza_pigra(primary) -> None:
    try:
        await scadi_inviti(primary, SCADENZA_PIGRA_LOTTO)
    except Exception as exc:  # noqa: BLE001 — lo recupera lo scheduler
        logger.warning("candidature: scadenza pigra degli inviti non riuscita (%s)",
                       getattr(exc, "code", None) or type(exc).__name__)


def _filtro_direzione(direzione: Direzione, company_id: str) -> str:
    """`or` PostgREST delle righe inviate o ricevute dall'azienda (uuid già
    validato: niente iniezioni nel filtro)."""
    mittente = {"candidatura": "company_profile_id", "invito": "creatore_company_profile_id"}
    destinataria = {"candidatura": "creatore_company_profile_id", "invito": "company_profile_id"}
    colonne = mittente if direzione == "inviate" else destinataria
    return ",".join(f"and(tipo.eq.{tipo},{colonna}.eq.{company_id})"
                    for tipo, colonna in colonne.items())


async def lista(
    primary, secondary, active, user: dict, *, direzione: Direzione, stato: str | None = None,
    tipo: str | None = None, call_id: Any = None, page: int = 1, page_size: int = 20,
) -> Page[CandidaturaOut]:
    """Candidature e inviti inviati o ricevuti dall'azienda attiva (titolare e
    membri in lettura), dal più recente, con la scadenza pigra degli inviti;
    `call_id` restringe a una call (la scheda «Candidature» del creatore)."""
    if not active.company_id:
        return Page.build([], 0, page, page_size)
    company_id = str(UUID(str(active.company_id)))
    await _scadenza_pigra(primary)
    offset = (page - 1) * page_size

    def costruisci():
        query = (
            primary.table("partner_candidature").select(CANDIDATURA_SELECT, count="exact")
            .or_(_filtro_direzione(direzione, company_id))
        )
        if stato:
            query = query.eq("stato", stato)
        if tipo:
            query = query.eq("tipo", tipo)
        if call_id:
            query = query.eq("partner_call_id", str(UUID(str(call_id))))
        return query.order("created_at", desc=True)

    lette, totale = await pagina(costruisci, offset, page_size)
    righe = [r for r in lette if isinstance(r, dict)]
    ctx = await _contesto(primary, righe, company_id)
    adesso = _adesso()
    items = [proietta(r, ctx, company_id=company_id, editable=bool(active.editable),
                      adesso=adesso) for r in righe if _lato(r, company_id)]
    return Page.build(items, totale or len(items), page, page_size)


async def dettaglio(primary, secondary, active, user: dict, candidatura_id: Any
                    ) -> CandidaturaOut:
    """Una candidatura per le sue due parti (404 per chiunque altro); il
    creatore vede anche il profilo pubblico della candidata finché è
    visibile."""
    company_id = _richiedi_azienda(active)
    await _scadenza_pigra(primary)
    riga = await _riga(primary, candidatura_id, company_id)
    return await _una_proiettata(primary, secondary, active, riga, con_profilo=True)


async def conteggi_riepilogo(primary, active) -> dict:
    """Per il badge del menu: inviti ricevuti in attesa (non scaduti) e
    candidature ricevute da decidere dall'azienda attiva. Best-effort: un
    errore vale 0 (il badge non deve rompere il menu)."""
    vuoto = {"inviti_ricevuti": 0, "candidature_da_decidere": 0}
    if not active.company_id:
        return vuoto
    company_id = str(active.company_id)
    try:
        inviti = await (
            primary.table("partner_candidature").select("id", count="exact")
            .eq("company_profile_id", company_id).eq("tipo", "invito").eq("stato", "inviata")
            .gt("scade_at", _adesso().isoformat()).limit(1).execute()
        )
        candidature = await (
            primary.table("partner_candidature").select("id", count="exact")
            .eq("creatore_company_profile_id", company_id).eq("tipo", "candidatura")
            .eq("stato", "inviata").limit(1).execute()
        )
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("candidature: conteggi del riepilogo non letti (%s)",
                       getattr(exc, "code", None) or type(exc).__name__)
        return vuoto
    return {"inviti_ricevuti": inviti.count or 0,
            "candidature_da_decidere": candidature.count or 0}


async def identita_se_rivelata(primary, candidatura_id: Any, company_id: str
                               ) -> IdentitaRivelataOut | None:
    """Identità dell'azienda `company_id` (la controparte di una candidatura
    accettata) SOLO se la rivelazione è accesa, la RPC ha scritto l'audit di
    rivelazione per quella candidatura (una rivelazione accesa dopo non
    tocca le accettazioni precedenti) E OGGI entrambe le aziende hanno
    ancora l'identità verificata dalla piattaforma (rivelazione simmetrica,
    WP9: una verifica revocata spegne l'identità nelle viste successive, gli
    audit restano; lettura non riuscita → nessuna identità). Ragione sociale,
    sito e PEC dal registro; nome e ruolo del referente (mai la sua email).
    Spenta: None, senza letture."""
    if not pps.RIVELAZIONE_IDENTITA_DISPONIBILE:
        return None
    audit = await (
        primary.table("audit_log").select("id,payload").eq("action", AUDIT_IDENTITA)
        .eq("payload->>candidatura_id", str(candidatura_id)).limit(1).execute()
    )
    if not audit.data:
        return None
    payload = audit.data[0].get("payload")
    if not isinstance(payload, dict):
        return None
    parti = {str(payload.get("company_profile_id") or ""),
             str(payload.get("creatore_company_profile_id") or "")} - {""}
    if len(parti) != 2 or str(company_id) not in parti:
        return None
    if len(await pps.aziende_con_identita_forte(primary, parti)) != 2:
        return None
    azienda = await _una(
        primary.table("company_profiles").select("id,parent_id,partita_iva").eq("id", company_id)
    )
    if azienda is None:
        return None
    dati = await _una(
        primary.table("company_data").select("piva_fetched,denominazione,raw")
        .eq("company_profile_id", company_id)
    )
    nome, ruolo = await _referente(primary, company_id, str(azienda.get("parent_id")))
    return identita_rivelata(azienda, dati, referente_nome=nome, referente_ruolo=ruolo)


def _nome_persona(riga: Mapping | None) -> str | None:
    nome = " ".join(
        p.strip() for p in ((riga or {}).get("nome"), (riga or {}).get("cognome"))
        if isinstance(p, str) and p.strip()
    )
    return nome or None


async def _referente(primary, company_id: str, owner_id: str) -> tuple[str | None, str]:
    """Nome e ruolo del referente (Q13): il membro indicato dal titolare se è
    ancora un membro attivo con visibilità sull'azienda, altrimenti il
    titolare. Mai email."""
    profilo = await _una(
        primary.table("company_partner_profiles").select("referente_user_id")
        .eq("company_profile_id", company_id)
    )
    referente = str((profilo or {}).get("referente_user_id") or "")
    if referente and referente != owner_id:
        membro = await _una(
            primary.table("family_members").select("id,member_id,denominazione")
            .eq("parent_id", owner_id).eq("member_id", referente).eq("status", "active")
        )
        accesso = await _una(
            primary.table("family_member_company_access").select("family_member_id")
            .eq("family_member_id", str(membro["id"])).eq("company_profile_id", company_id)
        ) if membro else None
        if membro and accesso:
            nome = (membro.get("denominazione") or "").strip() or None
            return nome, "referente"
    titolare = await _una(
        primary.table("profiles").select("id,nome,cognome").eq("id", owner_id)
    )
    return _nome_persona(titolare), "titolare"

