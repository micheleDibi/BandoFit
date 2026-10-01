"""Bozze AI dei documenti del partenariato (WP10, docs/partenariati.md W4,
T6-T8, Q7): lettera d'intenti, NDA e term sheet per chi partecipa a una call.

Chi (T3, T4, Q14): avvia SOLO il titolare dell'azienda attiva, se l'azienda
è la creatrice della call (in qualunque stato) o un membro non uscito del
consorzio (controparte accettata, call non sospesa); leggono anche i membri
con visibilità dell'azienda. Ogni azienda vede SOLO le proprie bozze; fuori
partecipazione 404, come una call che non esiste
(`partenariato_accesso.carica_call_autorizzata`). La RPC di prenotazione
ricontrolla tutto sotto i lock.

Flusso (T7, job asincrono come WP4/WP5):
1. `avvia` (202): input a WHITELIST (`partenariato_bozze_prompts`: tipo,
   bando, forma, ruoli e quote con segnaposto stabili; il nome della propria
   azienda solo con `includi_nome_azienda`), riserva al caso peggiore,
   prenotazione ATOMICA `fn_partner_bozza_prenota` (partecipazione, limite
   mensile del piano `partner_bozze_mese` sul pool del titolare, una sola
   bozza in preparazione per azienda × call × tipo, tetto giornaliero del
   titolare `partner_bozze_documento_limite_owner_giorno`, budget giornaliero
   del gruppo `altri`, fail-closed), poi il job in background;
2. `esegui_job`: modello → post-processing deterministico (segnaposto non
   previsti sostituiti, contatti e identificativi tolti con `anonimizza`,
   lunghezze) → chiusura ATOMICA di bozza ed esecuzione
   (`fn_partner_bozza_concludi`) → registro consumi, SOLO se la chiusura
   l'ha fatta lui (una riga per esecuzione);
3. `lista` / `stato` (poll-on-read) con il failsafe
   `fn_partner_bozza_chiudi_stale` sulle bozze orfane (anche nello
   scheduler, passo `failsafe_bozze`);
4. `pdf`: via `pdf_service`, con il disclaimer FISSO in testa e a piè di
   OGNI pagina e il nome del file generato dal server.

Costi (come WP3-WP5): modello non chiamato → 0 esplicito; richiesta
rifiutata dal provider con un 4xx non transitorio senza usage → 0 (respinta
prima della generazione); risposta arrivata ma inutilizzabile → max(reale,
riserva); timeout → riserva (`timeout_unknown`); errore transitorio del
provider (429, 5xx, 529) o di rete senza usage → costo ignoto (None: la
riserva resta nel budget, nel registro la riserva); `record_usage` su ogni
esito. Nel limite mensile del piano contano le bozze in errore con il modello
chiamato o a costo ignoto, tranne l'errore transitorio del provider
(`ai_non_disponibile`: ha risposto senza generare). Nessuna scrittura in
`ai_checks`.

Log: mai testi, nomi o contenuti; solo id e codici.
"""

import asyncio
import functools
import logging
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, NoReturn
from uuid import UUID
from zoneinfo import ZoneInfo

from postgrest.exceptions import APIError
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.errors import (
    AiNotConfiguredError,
    AiTimeoutError,
    AiUpstreamError,
    AppError,
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    PdfEngineUnavailableError,
    UpstreamError,
)
from app.schemas.partenariato_bozze import (
    DISCLAIMER,
    TIPI_BOZZA,
    BozzaOut,
    BozzeOut,
    SezioneBozzaOut,
)
from app.services import lookup_service, pdf_service
from app.services import partner_call_service as pcs
from app.services.ai_prezzi import costo_cents, stima_cents
from app.services.openapi_service import record_usage
from app.services.partenariati_ai_budget import budget_cents_gruppo
from app.services.partenariato_accesso import carica_call_autorizzata
from app.services.partenariato_anonimato import Identificativi, anonimizza, senza_invisibili
from app.services.partenariato_bozze_prompts import (
    BOZZE_PROMPT_VERSION,
    DA_COMPLETARE,
    SEGNAPOSTO_RE,
    SEGNAPOSTO_RIMOSSO,
    BozzaDocumentoAi,
    build_messaggio,
    costruisci_input,
    schema_bozza_json,
    segnaposto_ammessi,
    system_prompt,
)
from app.services.partenariato_errori import raise_from_rpc
from app.services.partenariato_service import non_transitorio, stato_http

logger = logging.getLogger("bandofit.partenariati")

SERVIZIO = "partner_bozza"
# Chi avvia (il titolare dell'azienda creatrice o di un membro del consorzio)
# e chi legge (in più i membri con visibilità dell'azienda creatrice; quelli
# della controparte hanno già il ruolo `controparte`, in sola lettura perché
# non `editable`).
RUOLI_AVVIO: frozenset[str] = frozenset({"creatore", "controparte"})
RUOLI_LETTURA: frozenset[str] = frozenset({"creatore", "titolare_o_membro", "controparte"})

MSG_SOLO_TITOLARE = "Le bozze dei documenti le prepara il titolare dell'azienda"
MSG_BOZZA_NON_TROVATA = "Bozza non trovata"
MSG_AI_NON_CONFIGURATA = "Generazione automatica non configurata su questo ambiente"
MSG_TIPO_NON_VALIDO = "Tipo di documento non valido"
MSG_NON_PRONTA = "La bozza non è ancora pronta: riprova tra qualche istante"

# Detail della RPC di prenotazione con un messaggio di altri WP nella mappa
# del modulo: qui si parla delle bozze dei documenti (code invariati, come
# `_ERRORI_CALL` nel WP5).
_ERRORI_BOZZE: dict[str, tuple[int, str, str]] = {
    "attore_non_titolare": (403, "forbidden", MSG_SOLO_TITOLARE),
    "funzione_non_inclusa": (
        409,
        "funzione_non_inclusa",
        "Il tuo piano non include le bozze dei documenti del partenariato",
    ),
    "bozza_in_corso": (
        409,
        "bozza_in_corso",
        "La bozza di questo documento è già in preparazione: attendi qualche istante",
    ),
    "ai_limite_owner": (
        429,
        "ai_limite_giornaliero",
        "Hai raggiunto le bozze di documenti di oggi: riprova domani",
    ),
    "ai_budget_esaurito": (
        429,
        "ai_sospesa_oggi",
        "La generazione automatica è sospesa per oggi: riprova domani",
    ),
}

# Codice di errore della bozza → messaggio per l'utente (BozzaOut.errore).
MESSAGGI_ERRORE = {
    "timeout": "La bozza ha richiesto troppo tempo: riprova più tardi",
    "interrotta": "La preparazione della bozza si è interrotta: riprova",
    "ai_risposta_non_valida": "La bozza non è venuta bene: riprova più tardi",
    "ai_non_disponibile": (
        "Il servizio che prepara la bozza non è disponibile: riprova più tardi"
    ),
    "ai_rete": "Il servizio che prepara la bozza non è disponibile: riprova più tardi",
    "ai_richiesta_rifiutata": (
        "Il servizio che prepara la bozza ha rifiutato la richiesta: riprova più tardi"
    ),
}
MESSAGGIO_ERRORE = "Non siamo riusciti a preparare la bozza: riprova più tardi"

# Tempo massimo per chiudere il job quando il task viene cancellato
# (spegnimento del processo): poi ci pensa il failsafe.
CHIUSURA_SU_CANCELLAZIONE_SECONDI = 5.0

# Lunghezze del contenuto salvato (il modello non ha vincoli nello schema).
MAX_TITOLO = 200
MAX_SEZIONI = 15
MAX_TITOLO_SEZIONE = 150
MAX_TESTO_SEZIONE = 6000
MAX_NOTE = 8
MAX_NOTA = 500
MAX_BOZZE_LISTA = 50

BOZZA_SELECT = (
    "id,partner_call_id,company_profile_id,tipo,stato,contenuto,errore,avviata_at,ready_at,"
    "created_at,includi_nome_azienda:input_snapshot->includi_nome_azienda"
)
MEMBRO_SELECT = "id,company_profile_id,ruolo,quota_percentuale,stato,created_at"

# Riferimenti ai job in corso: senza, il garbage collector può cancellare un
# task fire-and-forget a metà esecuzione.
_background_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    """Avvia il job in background (sostituibile nei test)."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def messaggio_errore(codice: str | None) -> str:
    return MESSAGGI_ERRORE.get(codice or "", MESSAGGIO_ERRORE)


def _adesso() -> datetime:
    return datetime.now(timezone.utc)


def _ts(valore: Any) -> datetime | None:
    if isinstance(valore, datetime):
        return valore if valore.tzinfo else valore.replace(tzinfo=timezone.utc)
    if not isinstance(valore, str) or not valore:
        return None
    try:
        istante = datetime.fromisoformat(valore.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return istante if istante.tzinfo else istante.replace(tzinfo=timezone.utc)


def _errore_rpc(exc: APIError) -> NoReturn:
    detail = (exc.details or "").strip()
    if detail in _ERRORI_BOZZE:
        raise AppError(*_ERRORI_BOZZE[detail]) from exc
    raise_from_rpc(exc)


async def _rpc(primary, nome: str, parametri: dict) -> Any:
    try:
        resp = await primary.rpc(nome, parametri).execute()
    except APIError as exc:
        _errore_rpc(exc)
    return resp.data


def stima_riserva_cents(system: str, messaggio: str) -> int:
    """Riserva al caso peggiore: prompt di sistema + messaggio + schema e
    tutto l'output consentito (2,5 caratteri per token)."""
    settings = get_settings()
    return stima_cents(
        settings.partenariato_ai_model,
        len(system) + len(messaggio) + len(schema_bozza_json()),
        settings.partner_bozze_documento_max_tokens,
    )


def _normalizza_bozza(valore: Any) -> str:
    """Uuid in forma canonica; malformato = bozza inesistente (404, mai il
    22P02 → 502)."""
    try:
        return str(UUID(str(valore).strip()))
    except (ValueError, AttributeError, TypeError):
        raise NotFoundError(MSG_BOZZA_NON_TROVATA) from None


# ------------------------------------------------------------ post-processing


class BozzaNonValida(ValueError):
    """Risposta del modello conforme allo schema ma senza nessuna sezione
    utilizzabile: pagata, trattata come una risposta non valida."""


def _tronca(testo: str, massimo: int) -> str:
    """Taglia a `massimo` caratteri, all'ultimo spazio se possibile."""
    if len(testo) <= massimo:
        return testo
    taglio = testo[: massimo - 1]
    spazio = taglio.rfind(" ")
    if spazio > massimo // 2:
        taglio = taglio[:spazio]
    return taglio.rstrip() + "…"


def _paragrafi(testo: str) -> str:
    """Righe senza spazi ai bordi, al più una riga vuota tra i paragrafi."""
    righe = [" ".join(r.split()) for r in testo.splitlines()]
    uscita: list[str] = []
    for riga in righe:
        if riga or (uscita and uscita[-1]):
            uscita.append(riga)
    return "\n".join(uscita).strip()


def _chiave(segnaposto: str) -> str:
    return " ".join(segnaposto[1:-1].split()).casefold()


@dataclass
class _Conteggi:
    rimossi: int = 0
    sostituiti: int = 0


_PARTI_SEGNAPOSTO = re.compile(f"({SEGNAPOSTO_RE.pattern})")


def _tronca_testo(testo: str, massimo: int) -> str:
    """Come `_tronca`, ma senza lasciare un segnaposto tagliato a metà."""
    tagliato = _tronca(testo, massimo)
    if tagliato != testo:
        aperta = tagliato.rfind("[")
        if aperta > tagliato.rfind("]"):
            tagliato = tagliato[:aperta].rstrip() + "…"
    return tagliato


def _contatti(testo: str) -> set[str]:
    return set(anonimizza(testo, None)[1]) if testo else set()


def _unisci_contatti_spezzati(testo: str) -> str:
    """Due righe consecutive si uniscono (con uno spazio) se SOLO insieme
    contengono un contatto («+39 333» a capo «1234567»): così il controllo sul
    testo intero lo vede e lo toglie. Le altre righe restano come sono."""
    righe = testo.split("\n")
    uscita = righe[:1]
    visti = _contatti(righe[0])
    for riga in righe[1:]:
        propri = _contatti(riga)
        if uscita[-1] and riga and _contatti(f"{uscita[-1]} {riga}") - visti - propri:
            uscita[-1] = f"{uscita[-1]} {riga}"
            visti = _contatti(uscita[-1])
        else:
            uscita.append(riga)
            visti = propri
    return "\n".join(uscita)


def _pulisci(testo: Any, *, ammessi: Mapping[str, str], ident: Identificativi | None,
             massimo: int, conteggi: _Conteggi, una_riga: bool = False) -> str:
    """Testo del modello → testo salvato: spazi compattati PRIMA dei
    controlli (un campo su una riga diventa una riga sola, un contatto
    spezzato da un a capo si riunisce); contatti tolti con `anonimizza` sul
    testo INTERO (un'email o un URL offuscati con le quadre, «nome [at]
    dominio [dot] it», si vedono per intero); segnaposto ammessi nella forma
    canonica e gli altri sostituiti con «[da completare]»; identificativi
    della propria azienda (se `ident`) tolti SOLO dal testo tra i segnaposto
    (un segnaposto sopravvive anche se la ragione sociale è una parola come
    «Partner» o «Data»); troncato."""
    if not isinstance(testo, str):
        return ""
    testo = senza_invisibili(testo)
    testo = " ".join(testo.split()) if una_riga else _unisci_contatti_spezzati(_paragrafi(testo))
    testo, rimossi = anonimizza(testo, None)
    conteggi.rimossi += len(rimossi)
    parti: list[str] = []
    for indice, parte in enumerate(_PARTI_SEGNAPOSTO.split(testo)):
        if indice % 2:  # un «[…]»
            canonico = ammessi.get(_chiave(parte))
            if canonico is None:
                conteggi.sostituiti += 1
                canonico = DA_COMPLETARE
            parti.append(canonico)
        elif parte:
            pulita, rimossi = anonimizza(parte, ident)
            conteggi.rimossi += len(rimossi)
            parti.append(pulita)
    testo = "".join(parti)
    testo = " ".join(testo.split()) if una_riga else _paragrafi(testo)
    return _tronca_testo(testo, massimo)


def post_bozza(bozza: BozzaDocumentoAi, *, tipo: str, ammessi: Iterable[str],
               ident: Identificativi | None) -> dict:
    """Bozza del modello post-validata → `contenuto` salvato: titolo,
    sezioni (al più `MAX_SEZIONI`, quelle vuote scartate), note per l'utente
    e avvisi su ciò che è stato tolto o sostituito. `ident` = identificativi
    della PROPRIA azienda quando l'utente non ha chiesto di includerne il
    nome (None altrimenti: si tolgono solo i contatti). Nessuna sezione
    utilizzabile → `BozzaNonValida`. Il disclaimer NON è qui."""
    # «[rimosso]» è il segnaposto che lascia il controllo dei contatti.
    mappa = {_chiave(s): s for s in (*ammessi, SEGNAPOSTO_RIMOSSO)}
    conteggi = _Conteggi()

    def pulisci(testo: Any, massimo: int, *, una_riga: bool = False) -> str:
        return _pulisci(testo, ammessi=mappa, ident=ident, massimo=massimo,
                        conteggi=conteggi, una_riga=una_riga)

    sezioni: list[dict] = []
    scartate = 0
    for voce in bozza.sezioni:
        testo = pulisci(voce.testo, MAX_TESTO_SEZIONE) if len(sezioni) < MAX_SEZIONI else ""
        if not testo:
            scartate += 1
            continue
        titolo = pulisci(voce.titolo, MAX_TITOLO_SEZIONE, una_riga=True)
        sezioni.append({"titolo": titolo or f"Sezione {len(sezioni) + 1}", "testo": testo})
    if not sezioni:
        raise BozzaNonValida("nessuna sezione utilizzabile")
    titolo = pulisci(bozza.titolo, MAX_TITOLO, una_riga=True) or TIPI_BOZZA[tipo]
    note = [n for n in (pulisci(v, MAX_NOTA, una_riga=True)
                        for v in bozza.note_per_l_utente[: MAX_NOTE * 2]) if n][:MAX_NOTE]
    avvisi: list[str] = []
    if conteggi.rimossi:
        avvisi.append(
            f"Abbiamo tolto {conteggi.rimossi} riferimenti non ammessi (contatti o dati che "
            "identificano un'azienda): al loro posto trovi «[rimosso]»"
        )
    if conteggi.sostituiti:
        avvisi.append(
            f"{conteggi.sostituiti} segnaposto non previsti sono stati sostituiti con "
            f"«{DA_COMPLETARE}»"
        )
    if scartate:
        avvisi.append(f"{scartate} sezioni vuote o in eccesso sono state tolte")
    return {
        "titolo": titolo,
        "sezioni": sezioni,
        "note_per_l_utente": note,
        "avvisi": avvisi,
    }


# ------------------------------------------------------------ esecuzione


@dataclass(frozen=True)
class RichiestaBozza:
    """Tutto ciò che serve al job, raccolto alla prenotazione: il job non
    rilegge nulla prima della chiusura."""

    bozza_id: str
    esecuzione_id: str
    call_id: str
    company_id: str
    owner_id: str
    user_id: str
    tipo: str
    system: str
    messaggio: str
    riserva_cents: int
    post: Callable[[BaseModel], dict]


@dataclass
class _Chiusura:
    """Una chiusura del job: bozza, esecuzione nel registro unico della spesa
    e riga del registro consumi da scrivere se l'esecuzione la chiude lei."""

    bozza_stato: str  # ready | error
    stato: str  # stato finale dell'esecuzione
    costo: int | None  # None = costo ignoto: la riserva resta nel budget
    costo_registro: int  # api_usage_events: mai ignoto (la riserva, al peggio)
    outcome: str  # success | error | timeout_unknown
    meta: dict
    contenuto: dict | None = None
    bozza_errore: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    model: str | None = None
    errore: str | None = None


@dataclass
class _Job:
    """Cosa sa il job, per la chiusura su cancellazione: l'usage della
    risposta, se la richiesta può essere partita e quali passi ha già
    TENTATO (nessuno si ripete)."""

    usage: object = None
    inviata: bool = False
    chiusura: _Chiusura | None = None
    esito: dict | None = None
    registrata: bool = False


async def _chiudi(primary, job: _Job, r: RichiestaBozza, chiusura: _Chiusura) -> dict | None:
    """Chiusura ATOMICA (`fn_partner_bozza_concludi`). Se non riesce restano
    in corso bozza ed esecuzione e li chiude il failsafe, che registra lui il
    consumo. Ritorna `{bozza_scritta, esecuzione_chiusa}` o None."""
    job.chiusura, job.esito = chiusura, None
    try:
        resp = await primary.rpc(
            "fn_partner_bozza_concludi",
            {
                "p_bozza": r.bozza_id,
                "p_esecuzione_id": r.esecuzione_id,
                "p_bozza_stato": chiusura.bozza_stato,
                "p_contenuto": chiusura.contenuto,
                "p_bozza_errore": chiusura.bozza_errore,
                "p_stato": chiusura.stato,
                "p_cost_cents": chiusura.costo,
                "p_input_tokens": chiusura.input_tokens,
                "p_output_tokens": chiusura.output_tokens,
                "p_model": chiusura.model,
                "p_errore": chiusura.errore,
            },
        ).execute()
    except Exception as exc:  # noqa: BLE001 — ci pensa il failsafe
        logger.error("bozze: chiusura della bozza %s non riuscita (%s)", r.bozza_id,
                     type(exc).__name__)
        return None
    job.esito = resp.data if isinstance(resp.data, dict) else {}
    return job.esito


async def _registra_chiusura(primary, job: _Job, r: RichiestaBozza) -> None:
    """Registro consumi dell'ultima chiusura, SOLO se ha chiuso lei
    l'esecuzione e mai due volte (il flag si alza PRIMA dell'insert)."""
    chiusura, esito = job.chiusura, job.esito
    if job.registrata or chiusura is None or not esito or not esito.get("esecuzione_chiusa"):
        return
    job.registrata = True
    outcome, meta = chiusura.outcome, chiusura.meta
    if chiusura.bozza_stato == "ready" and not esito.get("bozza_scritta"):
        # Il failsafe (o una nuova prenotazione) ha chiuso la bozza durante
        # la chiamata: il risultato pagato va perso, la spesa no.
        outcome, meta = "error", {**meta, "esito": "superata"}
    await record_usage(
        primary,
        user_id=r.user_id,
        family_parent_id=r.owner_id,
        service=SERVIZIO,
        outcome=outcome,
        cost_cents=int(chiusura.costo_registro),
        meta=meta,
        provider="anthropic",
    )


async def esegui_job(primary, ai, r: RichiestaBozza) -> str:
    """Chiamata al modello → post-processing → chiusura atomica → registro
    consumi. Non solleva MAI (salvo la cancellazione del task, dopo aver
    chiuso e registrato ciò che mancava). Ritorna l'esito (pronta, superata,
    timeout, errore)."""
    settings = get_settings()
    modello = settings.partenariato_ai_model
    meta = {
        "company_profile_id": r.company_id,
        "call_id": r.call_id,
        "bozza_id": r.bozza_id,
        "esecuzione_id": r.esecuzione_id,
        "tipo": r.tipo,
        "model": modello,
        "prompt_version": BOZZE_PROMPT_VERSION,
    }
    job = _Job()

    async def chiudi(chiusura: _Chiusura) -> dict | None:
        esito = await _chiudi(primary, job, r, chiusura)
        await _registra_chiusura(primary, job, r)
        return esito

    def non_valida(uso, costo_reale: int) -> _Chiusura:
        """Risposta arrivata ma inutilizzabile: pagata, max(reale, riserva)."""
        costo = max(costo_reale, r.riserva_cents)
        return _Chiusura(
            bozza_stato="error", bozza_errore="ai_risposta_non_valida", stato="errore",
            costo=costo, costo_registro=costo, outcome="error",
            meta={**meta, "esito": "errore", "errore": "ai_risposta_non_valida",
                  "costo_ignoto": False},
            input_tokens=uso.input_tokens, output_tokens=uso.output_tokens, model=modello,
            errore="ai_risposta_non_valida",
        )

    try:
        try:
            job.inviata = True  # la richiesta al modello può essere partita
            risposta, usage = await ai.genera(
                r.system,
                r.messaggio,
                BozzaDocumentoAi,
                model=modello,
                max_tokens=settings.partner_bozze_documento_max_tokens,
                timeout=settings.partner_bozze_documento_timeout_seconds,
            )
            job.usage = usage
            costo = costo_cents(modello, usage.input_tokens, usage.output_tokens)
            try:
                contenuto = r.post(risposta)
            except BozzaNonValida:
                await chiudi(non_valida(usage, costo))
                return "errore"
            esito = await chiudi(_Chiusura(
                bozza_stato="ready", contenuto=contenuto, stato="conclusa", costo=costo,
                costo_registro=costo, outcome="success",
                meta={**meta, "esito": "pronta", "input_tokens": usage.input_tokens,
                      "output_tokens": usage.output_tokens},
                input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                model=modello,
            ))
            if esito is None:
                return "errore"  # restano in corso: failsafe
            if not esito.get("bozza_scritta"):
                logger.warning("bozze: bozza %s superata durante la generazione", r.bozza_id)
                return "superata"
            return "pronta"
        except AiTimeoutError:
            # Esito e addebito ignoti: si registra il caso peggiore, la riserva.
            await chiudi(_Chiusura(
                bozza_stato="error", bozza_errore="timeout", stato="timeout",
                costo=r.riserva_cents, costo_registro=r.riserva_cents,
                outcome="timeout_unknown", meta={**meta, "esito": "timeout"}, model=modello,
                errore="timeout",
            ))
            return "timeout"
        except AiUpstreamError as exc:
            uso = exc.usage
            if uso is not None:
                # La risposta è arrivata (troncata o non valida): pagata.
                await chiudi(non_valida(
                    uso, costo_cents(modello, uso.input_tokens, uso.output_tokens)))
                return "errore"
            risposta_http = stato_http(exc)
            if non_transitorio(risposta_http):
                # Richiesta rifiutata dal provider (4xx non transitorio, es.
                # 400 invalid_request_error): respinta prima della
                # generazione, nessun token. Costo 0 ESPLICITO (come il WP3):
                # la riserva esce dal budget e la bozza non conta nel limite.
                costo, codice = 0, "ai_richiesta_rifiutata"
            elif risposta_http is not None:
                # Il provider ha risposto con un errore transitorio (429,
                # 5xx, 529 overloaded) senza generare nulla: costo ignoto per
                # il budget (la riserva resta), ma la bozza NON conta nel
                # limite mensile (fn_partner_bozze_usate).
                costo, codice = None, "ai_non_disponibile"
            else:
                # Nessuna risposta HTTP (rete): la richiesta può essere partita
                # e addebitata. Costo ignoto, conta nel limite.
                costo, codice = None, "ai_rete"
            await chiudi(_Chiusura(
                bozza_stato="error", bozza_errore=codice, stato="errore", costo=costo,
                costo_registro=r.riserva_cents if costo is None else costo, outcome="error",
                meta={**meta, "esito": "errore", "errore": codice, "costo_ignoto": costo is None,
                      "stato_http": risposta_http},
                model=modello, errore=codice,
            ))
            return "errore"
        except Exception as exc:
            logger.error("bozze: bozza %s non riuscita (%s)", r.bozza_id, type(exc).__name__)
            usage = job.usage
            if usage is not None:
                # Chiamata riuscita e pagata, guasto dopo: max(reale, riserva).
                costo = max(costo_cents(modello, usage.input_tokens, usage.output_tokens),
                            r.riserva_cents)
            elif job.inviata and not isinstance(exc, AiNotConfiguredError):
                costo = None
            else:
                costo = 0  # il modello non è stato chiamato
            await chiudi(_Chiusura(
                bozza_stato="error", bozza_errore="errore_interno", stato="errore",
                costo=costo, costo_registro=r.riserva_cents if costo is None else costo,
                outcome="error", meta={**meta, "esito": "errore", "costo_ignoto": costo is None},
                input_tokens=usage.input_tokens if usage else 0,
                output_tokens=usage.output_tokens if usage else 0,
                model=modello if costo != 0 else None, errore="errore_interno",
            ))
            return "errore"
    except asyncio.CancelledError:
        # Task cancellato (spegnimento, deploy) in QUALUNQUE punto, anche
        # dentro un ramo d'errore: si completa ciò che manca, poi si rilancia.
        await _chiudi_su_cancellazione(primary, job, r, modello=modello, meta=meta)
        raise


async def _chiudi_su_cancellazione(
    primary, job: _Job, r: RichiestaBozza, *, modello: str, meta: dict
) -> None:
    """Chiusura best-effort di un job il cui task è stato cancellato, con un
    tempo massimo, senza ripetere i passi già tentati: registro già scritto →
    niente; chiusura già tornata → si registra la sua; altrimenti si chiude
    come interrotta (la RPC è idempotente). Se non riesce, il failsafe."""
    if job.registrata:
        return
    usage = job.usage
    if usage is not None:
        costo = max(costo_cents(modello, usage.input_tokens, usage.output_tokens),
                    r.riserva_cents)
        outcome, tokens = "error", (usage.input_tokens, usage.output_tokens)
    elif job.inviata:
        # Chiamata forse partita e forse addebitata: come un timeout.
        costo, outcome, tokens = r.riserva_cents, "timeout_unknown", (0, 0)
    else:
        costo, outcome, tokens = 0, "error", (0, 0)
    try:
        async with asyncio.timeout(CHIUSURA_SU_CANCELLAZIONE_SECONDI):
            if job.esito is None:
                await _chiudi(primary, job, r, _Chiusura(
                    bozza_stato="error", bozza_errore="interrotta", stato="interrotta",
                    costo=costo, costo_registro=costo, outcome=outcome,
                    meta={**meta, "esito": "interrotta"}, input_tokens=tokens[0],
                    output_tokens=tokens[1], model=modello if costo else None,
                    errore="interrotta",
                ))
            await _registra_chiusura(primary, job, r)
    except Exception:  # noqa: BLE001 — il failsafe chiuderà la bozza
        logger.warning("bozze: chiusura dopo la cancellazione non riuscita (bozza %s)",
                       r.bozza_id)


# ------------------------------------------------------------ letture DB


async def _membri(primary, call_id: str) -> list[dict]:
    """Righe del consorzio (solo ruolo, quota, stato e ordine d'ingresso:
    mai nomi né dati dei membri)."""
    resp = (
        await primary.table("partner_call_membri").select(MEMBRO_SELECT)
        .eq("partner_call_id", call_id).execute()
    )
    return [r for r in resp.data or [] if isinstance(r, dict)]


def _nomi_programmi(lookups) -> dict[int, str]:
    """Nomi dei programmi del catalogo dai lookup (il programma è facoltativo
    nell'input: le voci malformate si saltano)."""
    programmi: dict[int, str] = {}
    for voce in getattr(lookups, "programmi", None) or []:
        id_, nome = getattr(voce, "id", None), getattr(voce, "nome", None)
        if isinstance(id_, int) and isinstance(nome, str):
            programmi[id_] = nome
    return programmi


async def _bozze(primary, call_id: str, company_id: str) -> list[dict]:
    resp = (
        await primary.table("partner_bozze_documento").select(BOZZA_SELECT)
        .eq("partner_call_id", call_id).eq("company_profile_id", company_id)
        .order("created_at", desc=True).limit(MAX_BOZZE_LISTA).execute()
    )
    return [r for r in resp.data or [] if isinstance(r, dict)]


async def _bozza(primary, call_id: str, company_id: str, bozza_id: str) -> dict:
    """La bozza se è dell'azienda attiva su QUESTA call (404 altrimenti)."""
    resp = (
        await primary.table("partner_bozze_documento").select(BOZZA_SELECT)
        .eq("id", bozza_id).eq("partner_call_id", call_id)
        .eq("company_profile_id", company_id).limit(1).execute()
    )
    riga = resp.data[0] if resp.data else None
    if not isinstance(riga, dict):
        raise NotFoundError(MSG_BOZZA_NON_TROVATA)
    return riga


async def _failsafe(primary, righe: Iterable[Mapping],
                    ricarica: Callable[[], Awaitable[Any]]) -> Any:
    """Bozze `pending` oltre `partner_bozze_documento_stale_minuti` (processo
    riavviato): le chiude la RPC (errore `interrotta`, costo ignoto, riga
    `timeout_unknown`) e si rilegge. Best-effort: senza orfane nessuna RPC."""
    minuti = get_settings().partner_bozze_documento_stale_minuti
    soglia = _adesso() - timedelta(minutes=minuti)
    orfane = any(
        r.get("stato") == "pending"
        and ((avviata := _ts(r.get("avviata_at"))) is None or avviata <= soglia)
        for r in righe
    )
    if not orfane:
        return None
    try:
        await primary.rpc("fn_partner_bozza_chiudi_stale", {"p_minuti": minuti}).execute()
        return await ricarica()
    except Exception:
        logger.exception("bozze: failsafe delle bozze non riuscito")
        return None


def _out(riga: Mapping) -> BozzaOut:
    """Proiezione a whitelist di una riga (mai input, costi, utenti)."""
    stato = riga.get("stato")
    contenuto = riga.get("contenuto") if stato == "ready" else None
    contenuto = contenuto if isinstance(contenuto, Mapping) else {}
    sezioni = [
        SezioneBozzaOut(titolo=s["titolo"], testo=s["testo"])
        for s in contenuto.get("sezioni") or []
        if isinstance(s, Mapping) and isinstance(s.get("titolo"), str)
        and isinstance(s.get("testo"), str)
    ]

    def stringhe(chiave: str) -> list[str]:
        return [v for v in contenuto.get(chiave) or [] if isinstance(v, str)]

    titolo = contenuto.get("titolo")
    return BozzaOut(
        id=riga["id"],
        tipo=riga["tipo"],
        stato=stato,
        avviata_at=riga.get("avviata_at") or riga.get("created_at"),
        conclusa_at=riga.get("ready_at"),
        errore=messaggio_errore(riga.get("errore")) if stato == "error" else None,
        includi_nome_azienda=riga.get("includi_nome_azienda") is True,
        titolo=titolo if isinstance(titolo, str) and stato == "ready" else None,
        sezioni=sezioni,
        note_per_l_utente=stringhe("note_per_l_utente"),
        avvisi=stringhe("avvisi"),
    )


# ------------------------------------------------------------ operazioni


def _nome_azienda(az: "pcs.Azienda") -> str | None:
    """Il nome della propria azienda da mettere nella bozza (solo su
    richiesta): la denominazione del Registro Imprese se i dati importati sono
    dell'azienda (T5), altrimenti la ragione sociale."""
    registro = az.dati_registro or {}
    for valore in (registro.get("denominazione"), az.company.get("ragione_sociale")):
        if isinstance(valore, str) and valore.strip():
            return valore.strip()
    return None


async def avvia(primary, secondary, ai, active, user: dict, call_id: Any, tipo: str, *,
                includi_nome_azienda: bool = False) -> BozzaOut:
    """202: prenota (fail-closed) e avvia in background la bozza. Errori:
    403 `forbidden` (non titolare), 404 (call fuori partecipazione), 400
    (tipo), 503 `ai_not_configured`, 409 `funzione_non_inclusa` /
    `bozze_esaurite` / `bozza_in_corso`, 429 `ai_limite_giornaliero` (tetto
    giornaliero del titolare) / `ai_sospesa_oggi`."""
    if not active.editable:
        raise ForbiddenError(MSG_SOLO_TITOLARE)
    if tipo not in TIPI_BOZZA:
        raise BadRequestError(MSG_TIPO_NON_VALIDO)
    call, _ = await carica_call_autorizzata(primary, call_id, active, user, ammessi=RUOLI_AVVIO)
    if not ai.enabled:
        raise AiNotConfiguredError(MSG_AI_NON_CONFIGURATA)
    company_id = str(active.company_id)
    # Lookup fail-closed, PRIMA della prenotazione e della chiamata pagata:
    # senza (cache vuota, catalogo non leggibile) niente bozza (503).
    programmi = _nomi_programmi(await lookup_service.get_lookups(secondary))
    membri, az = await asyncio.gather(
        _membri(primary, str(call["id"])),
        pcs.carica_azienda(primary, company_id, active.owner_id),
    )
    nome = _nome_azienda(az) if includi_nome_azienda else None
    snapshot = costruisci_input(tipo=tipo, call=call, membri=membri, company_id=company_id,
                                programmi=programmi, nome_azienda=nome)
    system = system_prompt(tipo)
    messaggio = build_messaggio(snapshot)
    riserva = stima_riserva_cents(system, messaggio)
    esito = await _rpc(primary, "fn_partner_bozza_prenota", {
        "p_owner": str(active.owner_id),
        "p_company": company_id,
        "p_call": str(call["id"]),
        "p_richiedente": str(user["id"]),
        "p_tipo": tipo,
        "p_input": snapshot,
        "p_budget_cents": budget_cents_gruppo("altri"),
        "p_costo_riservato_cents": riserva,
        "p_prompt_version": BOZZE_PROMPT_VERSION,
        "p_limite_owner": get_settings().partner_bozze_documento_limite_owner_giorno,
    })
    bozza_id = esito.get("bozza_id") if isinstance(esito, dict) else None
    esecuzione_id = esito.get("esecuzione_id") if isinstance(esito, dict) else None
    if not (isinstance(bozza_id, str) and bozza_id and isinstance(esecuzione_id, str)
            and esecuzione_id):
        # Senza bozza ed esecuzione registrate la spesa non sarebbe contata.
        raise UpstreamError()
    # Con il nome della propria azienda chiesto dall'utente si tolgono solo i
    # contatti; altrimenti anche gli identificativi dell'azienda.
    post = functools.partial(post_bozza, tipo=tipo, ammessi=segnaposto_ammessi(snapshot),
                             ident=None if snapshot["includi_nome_azienda"] else az.ident)
    _spawn(esegui_job(primary, ai, RichiestaBozza(
        bozza_id=bozza_id, esecuzione_id=esecuzione_id, call_id=str(call["id"]),
        company_id=company_id, owner_id=str(active.owner_id), user_id=str(user["id"]),
        tipo=tipo, system=system, messaggio=messaggio, riserva_cents=riserva, post=post,
    )))
    return BozzaOut(id=bozza_id, tipo=tipo, stato="pending", avviata_at=_adesso(),
                    includi_nome_azienda=bool(snapshot["includi_nome_azienda"]))


async def lista(primary, active, user: dict, call_id: Any) -> BozzeOut:
    """Le bozze dell'azienda attiva sulla call (poll-on-read + failsafe)."""
    call, ruolo = await carica_call_autorizzata(primary, call_id, active, user,
                                                ammessi=RUOLI_LETTURA)
    cid, company = str(call["id"]), str(active.company_id)
    righe = await _bozze(primary, cid, company)
    rilette = await _failsafe(primary, righe, lambda: _bozze(primary, cid, company))
    if rilette is not None:
        righe = rilette
    return BozzeOut(bozze=[_out(r) for r in righe],
                    editable=bool(active.editable) and ruolo in RUOLI_AVVIO)


async def stato(primary, active, user: dict, call_id: Any, bozza_id: Any) -> BozzaOut:
    """Una bozza dell'azienda attiva (poll-on-read + failsafe)."""
    call, _ = await carica_call_autorizzata(primary, call_id, active, user,
                                            ammessi=RUOLI_LETTURA)
    cid, company, bid = str(call["id"]), str(active.company_id), _normalizza_bozza(bozza_id)
    riga = await _bozza(primary, cid, company, bid)
    riletta = await _failsafe(primary, [riga], lambda: _bozza(primary, cid, company, bid))
    return _out(riletta if riletta is not None else riga)


def _paragrafi_pdf(testo: str) -> list[str]:
    return [p for p in (" ".join(r.split()) for r in testo.split("\n")) if p]


def documento_pdf(bozza: BozzaOut, data: str) -> pdf_service.PdfDoc:
    """Il documento del PDF: disclaimer FISSO in testa (prima sezione), poi
    le sezioni della bozza e le note per l'utente; il piè di pagina lo mette
    `_disclaimer_su_ogni_pagina`. Solo il contenuto già ripulito e testi
    della piattaforma."""
    etichetta = TIPI_BOZZA[bozza.tipo]
    sezioni = [pdf_service.section("Avvertenza", [pdf_service.text_block(DISCLAIMER)])]
    for voce in bozza.sezioni:
        sezioni.append(pdf_service.section(
            voce.titolo, [pdf_service.text_block(p) for p in _paragrafi_pdf(voce.testo)]))
    if bozza.note_per_l_utente:
        sezioni.append(pdf_service.section(
            "Note per completare la bozza",
            [pdf_service.text_block(f"• {n}") for n in bozza.note_per_l_utente]))
    return pdf_service.PdfDoc(
        title=bozza.titolo or etichetta,
        subtitle=f"{etichetta} · bozza del {data}",
        badges=["Bozza da rivedere"],
        sections=[s for s in sezioni if s is not None],
    )


# Piè di pagina del disclaimer: 18 mm dal bordo sinistro (il margine dei due
# motori), 6 mm dal basso (sotto il numero di pagina), Helvetica 7 pt.
_DISCLAIMER_X_PT = 51.0
_DISCLAIMER_Y_PT = 17.0


def _disclaimer_su_ogni_pagina(contenuto: bytes) -> bytes:
    """Il disclaimer FISSO a piè di OGNI pagina del PDF già composto, con
    qualunque motore (il `footer` di `pdf_service` è solo un paragrafo in
    coda): una pagina stampata o inoltrata da sola lo porta con sé."""
    import io

    from pypdf import PageObject, PdfReader, PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    testo = DISCLAIMER.encode("cp1252", errors="replace")
    testo = testo.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
        NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
    })
    scrittore = PdfWriter(clone_from=PdfReader(io.BytesIO(contenuto)))
    for pagina in scrittore.pages:
        box = pagina.mediabox
        sopra = PageObject.create_blank_page(width=float(box.width), height=float(box.height))
        sopra[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/FDisclaimer"): font})})
        flusso = DecodedStreamObject()
        posizione = b"%.2f %.2f" % (float(box.left) + _DISCLAIMER_X_PT,
                                     float(box.bottom) + _DISCLAIMER_Y_PT)
        flusso.set_data(b"q 0.39 0.45 0.55 rg BT /FDisclaimer 7 Tf " + posizione
                        + b" Td (" + testo + b") Tj ET Q")
        sopra[NameObject("/Contents")] = flusso
        pagina.merge_page(sopra)
    uscita = io.BytesIO()
    scrittore.write(uscita)
    return uscita.getvalue()


def _render(doc: pdf_service.PdfDoc) -> bytes:
    """`pdf_service.render` + il disclaimer su ogni pagina; se il piè di
    pagina non riesce, nessun PDF (503), mai uno senza avvertenza."""
    contenuto = pdf_service.render(doc)
    try:
        return _disclaimer_su_ogni_pagina(contenuto)
    except Exception as exc:
        logger.error("bozze: disclaimer sulle pagine del PDF non riuscito (%s)",
                     type(exc).__name__)
        raise PdfEngineUnavailableError("Impossibile generare il PDF in questo momento") from exc


def nome_file(bozza: BozzaOut, giorno: str) -> str:
    """Nome del file generato dal server: solo tipo e data (ASCII)."""
    return f"bozza-{bozza.tipo.replace('_', '-')}-{giorno}.pdf"


async def pdf(primary, active, user: dict, call_id: Any, bozza_id: Any) -> tuple[bytes, str]:
    """PDF di una bozza pronta dell'azienda attiva → (contenuto, nome del
    file). 409 `bozza_non_pronta` se non è pronta; 503 `pdf_unavailable` se
    nessun motore PDF è disponibile."""
    bozza = await stato(primary, active, user, call_id, bozza_id)
    if bozza.stato != "ready":
        raise AppError(409, "bozza_non_pronta", MSG_NON_PRONTA)
    istante = (bozza.conclusa_at or bozza.avviata_at).astimezone(ZoneInfo("Europe/Rome"))
    doc = documento_pdf(bozza, istante.strftime("%d/%m/%Y"))
    contenuto = await asyncio.to_thread(_render, doc)
    return contenuto, nome_file(bozza, istante.date().isoformat())
