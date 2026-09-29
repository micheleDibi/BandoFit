"""Call di partenariato (WP5, docs/partenariati.md §2.5 C1-C7).

Chi agisce (T4, Q14): scrive SOLO il titolare dell'azienda attiva
(`active.editable`); i membri con visibilità leggono. Ogni rotta carica la
call con `partenariato_accesso.carica_call_autorizzata` (fuori autorizzazione
→ 404) e ogni RPC la ricerca con id + azienda attiva + owner: un Advisor con
l'azienda A attiva non tocca le call di B.

Flusso del wizard (7 passi, stato nella bozza lato server):
1. `crea_bozza`: bando dal catalogo, stato LIVE da `bando_pubblico` (aperto o
   in apertura), estrazione WP3 `non_ammesso` → serve un motivo
   (`partenariato_non_ammesso` altrimenti); call SOLO anonime (WP4,
   `NOMINATIVO_DISPONIBILE`);
2. `conferma_regole`: snapshot delle regole confermate; una voce `confermata`
   deve coincidere con una voce VERIFICATA dell'estrazione corrente, e la
   `fonte` la scrive il servizio dalla riga `bando_partenariato`;
3. `genera_requisiti` / `salva_requisiti`: gap analysis deterministica (regole
   confermate, pre-check del catalogo, ultimo AI-check `ready`) con la
   copertura del creatore in vista «proprio»; regole finanziarie solo dallo
   snapshot (Q11);
4. e 5. `avvia_proposta_posizioni` / `avvia_proposta_testi`: job AI asincroni
   (202 + poll-on-read del dettaglio + failsafe), mai salvati da soli;
   `salva_posizioni` / `aggiorna` salvano ciò che il creatore conferma;
6. `anteprima`: la proiezione pubblica e i rilievi anti-contatti;
7. `pubblica`: controlli in Python (stato live del bando, `non_ammesso`,
   rilievi, Q11, scadenza di default) e poi la RPC (identità dal registro,
   completezza, limiti del piano sul pool dell'owner e, dalla 0039,
   esclusività sulle candidature accettate: `409 esclusivita_violata`).

Anti-contatti (C7): i testi pubblici (titolo, descrizione, profilo ideale,
titoli e note delle posizioni, etichette dei requisiti, testi dei requisiti
visibili ai terzi e di quelli scritti a mano) non ammettono contatti né
identificativi dell'azienda (le call sono anonime); i `dettagli_riservati`
possono nominare l'azienda ma non contenere contatti diretti. Controllo
sulla forma canonica del testo (`partenariato_accesso.rilievi_testo`).
Rilievo bloccante → 400 `testo_non_conforme`.

Chiusure automatiche (C6): `motivo_chiusura_auto` (puro) e
`chiudi_automaticamente` servono sia allo scheduler sia al controllo in
lettura del dettaglio; a ogni chiusura una notifica al creatore e al
titolare (`partenariato.call_chiusa`, dedup per call).

Segnalazioni (C7, DSA): `segnala` con rate limit anti-abuso, contenuto
visibile al segnalante (404 altrimenti), snapshot della proiezione pubblica e
conferma di ricezione in-app.

WP6 (bacheca, «Per te», suggeriti, call salvate): le letture passano
dall'indice in-process (`partenariato_indice`) e dal matching puro
(`partenariato_matching`); ogni pagina restituita è ricontrollata live
(call ancora pubblicate, candidati ancora con l'opt-in). Il dettaglio di una
call di un altro owner apre la vista PUBBLICA (ruolo `pubblico`: pubblicata,
visibile a tutti, non sospesa) con il match dell'azienda attiva. Dopo la
pubblicazione: indice invalidato e, in background, chiavi dei collegamenti
del creatore e fan-out delle notifiche proattive; dopo una modifica della
call pubblicata o la sua chiusura: indice invalidato e notifica a chi l'ha
salvata.

WP7: il dettaglio riconosce anche la controparte accettata
(`CallVistaControparteOut`: vista pubblica più riservati e budget esatto;
identità solo con la rivelazione accesa e il suo audit; mai per una call
sospesa) e chi ha una candidatura o un invito in attesa (vista pubblica con
lo stato della propria candidatura, anche per una call solo su invito; una
riga già chiusa si vede solo finché la call è visibile a tutti) e porta i
requisiti dichiarabili; i suggeriti portano lo stato del contatto; il
riepilogo conta inviti
ricevuti, candidature da decidere e messaggi non letti; si segnalano anche i
messaggi della chat (solo quelli dell'altra azienda, solo dalle parti della
conversazione).

WP8: le card (le mie, bacheca, «Per te») contano le candidature spontanee
ricevute (in attesa o accettate), con una lettura per pagina fuori dalla
ricarica dell'indice.

Log: mai testi, P.IVA, nomi o importi; solo id e codici.
"""

import asyncio
import functools
import logging
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, NoReturn

from postgrest.exceptions import APIError
from pydantic import ValidationError

from app.core.config import get_settings
from app.core.errors import (
    AiNotConfiguredError,
    AppError,
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    UpstreamError,
)
from app.schemas.common import Page
from app.schemas.partenariato_criteri import ProfiloCandidato
from app.schemas.partner_call import (
    AiCheckGapOut,
    AnteprimaOut,
    BandoCallOut,
    CallAggiornaIn,
    CallCardOut,
    CallCreaIn,
    CallPubblicaOut,
    CallVistaCreatoreOut,
    ChiudiIn,
    FonteSnapshot,
    GapOut,
    JobPosizioniOut,
    JobTestiOut,
    MotivoBloccoOut,
    PartenariatoGapOut,
    PosizioneOut,
    PosizioniIn,
    PropostaPosizioniOut,
    PropostaTestiOut,
    RegoleCallSnapshot,
    RegoleConfermaIn,
    RequisitiIn,
    RequisitoOut,
    RilievoOut,
    SegnalazioneIn,
    SegnalazioneOut,
    VersioneOut,
    budget_nella_fascia,
)
from app.services import (
    ai_check_service,
    bandi_service,
    bando_fonti_service,
    bilanci_service,
    entitlement_service,
    lookup_service,
    partenariato_indice,
    partenariato_notifiche,
    partner_call_ai,
    rate_limit_service,
)
from app.services import partenariato_matching as pm
from app.services import partner_profile_service as pps
from app.services.bilanci_indicatori import calcola_fasce
from app.services.notification_service import notify
from app.services.openapi_mapping import build_dossier
from app.services.partenariati_ai_budget import budget_cents_gruppo
from app.services.partenariato_accesso import (
    CALL_SELECT,
    POSIZIONE_SELECT,
    REQUISITO_SELECT,
    RUOLI_AZIENDA,
    RUOLI_SCRITTURA,
    CallBachecaOut,
    CallPubblicaDettaglioOut,
    CallVistaControparteOut,
    PerTeOut,
    RiepilogoOut,
    SuggeritiOut,
    call_bacheca,
    call_card,
    call_pubblica,
    candidato_suggerito,
    candidatura_propria,
    candidatura_su_call,
    carica_call_autorizzata,
    creatore_pubblico,
    nomi_regioni,
    normalizza_id,
    proietta_versione,
    pubblicamente_visibile,
    requisiti_dichiarabili,
    requisito_visibile,
    rilievi_testo,
    stato_effettivo,
    vista_controparte,
)
from app.services.partenariato_anonimato import (
    ETICHETTE_RILIEVO,
    Identificativi,
    identificativi_azienda,
)
from app.services.partenariato_criteri import intervallo_costo_quota, profilo_candidato_da
from app.services.partenariato_errori import RPC_ERRORS, raise_from_rpc
from app.services.partner_call_gap import (
    RequisitoBozza,
    copertura_creatore,
    errori_regole_finanziarie,
    errori_voci_confermate,
    etichetta_breve,
    evidenze_assorbite,
    intervallo_budget,
    payload_requisito,
    requisiti_da_ai_check,
    requisiti_da_precheck,
    requisiti_da_regole,
    riepilogo,
    unisci,
)
from app.services.partner_call_prompts import (
    POSIZIONI_PROMPT_VERSION,
    SYSTEM_POSIZIONI,
    SYSTEM_TESTI,
    TESTI_PROMPT_VERSION,
    BozzaTestiCall,
    PropostaPosizioni,
    build_posizioni_input,
    build_testi_input,
    schema_posizioni_json,
    schema_testi_json,
)
from app.services.partner_profilo_pubblico import profilo_pubblico

logger = logging.getLogger("bandofit.partenariati")

MSG_SOLO_TITOLARE = "Le call di partenariato le gestisce il titolare dell'azienda"
MSG_SENZA_AZIENDA = "Nessuna azienda attiva: crea prima l'azienda"
MSG_NOMINATIVO_NON_DISPONIBILE = (
    "Per ora le call si pubblicano solo in forma anonima: il nome dell'azienda si rivela "
    "solo alle aziende che accetti"
)
MSG_AI_NON_CONFIGURATA = "Generazione automatica non configurata su questo ambiente"
MSG_NON_AMMESSO = (
    "Secondo l'analisi del bando il partenariato non è ammesso: se sei sicuro che lo "
    "ammetta, spiega il motivo"
)
MSG_BANDO_NON_VERIFICABILE = (
    "Non riusciamo a verificare lo stato del bando in questo momento: riprova tra poco"
)
MSG_SEGNALAZIONE_DOPPIA = "Hai già segnalato questo contenuto"
MSG_LIMITE_SEGNALAZIONI = "Hai inviato molte segnalazioni oggi: riprova domani"
MSG_TESTO_RIMOSSO = "[rimosso]"

STATI_BANDO_APERTI = frozenset({"aperto", "in apertura prossimamente"})
STATI_MODIFICABILI = frozenset({"bozza", "pubblicata"})
# Giorni di assenza del bando da `bando_pubblico` prima della chiusura (C6).
GIORNI_BANDO_MANCANTE = 7
FINESTRA_SEGNALAZIONI_SECONDI = 86_400
TIPO_NOTIFICA_CHIUSURA = "partenariato.call_chiusa"
TIPO_NOTIFICA_SEGNALAZIONE = "partenariato.segnalazione_ricevuta"

# Il `detail` delle RPC comuni con il profilo partner ha un messaggio sul
# profilo: qui si parla della call. Il code resta quello della mappa.
_ERRORI_CALL: dict[str, tuple[int, str, str]] = {
    "attore_non_titolare": (403, "forbidden", MSG_SOLO_TITOLARE),
    "identita_non_verificata": (
        409,
        "identita_non_verificata",
        "Per pubblicare una call importa prima i dati ufficiali dell'azienda dalla partita "
        "IVA: l'impresa deve risultare attiva nel Registro Imprese",
    ),
    "ai_limite_owner": (
        429,
        "ai_limite_giornaliero",
        "Hai raggiunto le proposte automatiche di oggi: riprova domani",
    ),
    "ai_budget_esaurito": (
        429,
        "ai_sospesa_oggi",
        "La generazione automatica è sospesa per oggi: riprova domani",
    ),
}

_MOTIVI_IDENTITA = {
    "dati_non_importati": "Importa i dati ufficiali dell'azienda dalla partita IVA",
    "piva_diversa": (
        "I dati ufficiali importati sono di un'altra partita IVA: importa di nuovo quelli "
        "dell'azienda"
    ),
    "impresa_non_attiva": "L'impresa non risulta attiva nel Registro Imprese",
    "dati_sandbox": "I dati importati vengono dall'ambiente di prova: importali di nuovo",
}

# Corpo della notifica di chiusura automatica, per motivo.
_CORPI_CHIUSURA = {
    "scadenza_call": "La call ha raggiunto la scadenza che avevi indicato.",
    "bando_chiuso": "Il bando della call si è chiuso.",
    "bando_sospeso": "Il bando della call è stato sospeso.",
    "bando_revocato": "Il bando della call è stato revocato.",
    "bando_non_disponibile": "Il bando della call non è più disponibile nel catalogo.",
    "azienda_non_disponibile": "L'azienda della call è stata eliminata o archiviata.",
}

# Rilievi che bloccano i dettagli riservati: solo i contatti diretti (possono
# nominare l'azienda: li vedranno solo le aziende accettate, WP7).
_CONTATTI = frozenset({"email", "telefono", "url", "iban", "dominio_bloccato"})

AZIENDA_SELECT = "id,parent_id,ragione_sociale,partita_iva,codice_fiscale,sito_web,settore_id"
COMPANY_DATA_SELECT = "raw,derived,piva_fetched,sandbox,denominazione,stato_impresa"
PERSONE_SELECT = "nome,cognome"
PROFILO_PARTNER_SELECT = "company_profile_id,tipi_soggetto,competenze,certificazioni,esperienze"
PARTENARIATO_SELECT = "bando_id,stato,esito,modalita_effettiva,regole,estratta_at,prompt_version"
LISTA_SELECT = (
    "id,bando_slug,bando_titolo,bando_scadenza,ruolo_creatore,titolo,budget_fascia,"
    "scadenza_call,stato,pubblicata_at,wizard_passo,created_at,updated_at"
)
BANDO_CATALOGO_SELECT = "id,slug,titolo,titolo_breve,programmi(id),tipologie_bando(id)"
BANDO_FACET_SELECT = (
    "id,slug,bando_regioni(regioni(id,nome)),bando_settori(settori(id,nome)),"
    "bando_beneficiari(beneficiari(id,nome)),bando_codici_ateco(codici_ateco(id,codice))"
)

# Riferimenti ai job in corso: senza, il garbage collector può cancellare un
# task fire-and-forget a metà esecuzione.
_background_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    """Avvia il job AI in background (sostituibile nei test)."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


# ------------------------------------------------------------------ utilità


def _adesso() -> datetime:
    return datetime.now(timezone.utc)


def _oggi() -> date:
    return bandi_service.today_italy()


def _data(valore: Any) -> date | None:
    if isinstance(valore, datetime):
        return valore.date()
    if isinstance(valore, date):
        return valore
    if not isinstance(valore, str) or not valore:
        return None
    try:
        return date.fromisoformat(valore[:10])
    except ValueError:
        return None


def _ts(valore: Any) -> datetime | None:
    if isinstance(valore, datetime):
        return valore if valore.tzinfo else valore.replace(tzinfo=timezone.utc)
    if not valore:
        return None
    try:
        parsed = datetime.fromisoformat(str(valore).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _decimale(valore: Any) -> Decimal | None:
    if valore is None or isinstance(valore, bool):
        return None
    try:
        return Decimal(str(valore))
    except (InvalidOperation, ValueError):
        return None


def _errore_rpc(exc: APIError) -> NoReturn:
    """Errore di una RPC delle call: messaggi della call per i detail comuni
    con il profilo partner, altrimenti la mappa del modulo."""
    detail = (exc.details or "").strip()
    if detail in _ERRORI_CALL:
        raise AppError(*_ERRORI_CALL[detail]) from exc
    raise_from_rpc(exc)


def _errore_mappa(detail: str) -> AppError:
    return AppError(*RPC_ERRORS[detail])


def _richiedi_titolare(active) -> None:
    if not active.editable:
        raise ForbiddenError(MSG_SOLO_TITOLARE)


def _richiedi_azienda(active) -> str:
    if not active.company_id:
        raise NotFoundError(MSG_SENZA_AZIENDA)
    return str(active.company_id)


def _nominativo(anonima: Any) -> None:
    """Call solo anonime (stessa scelta del profilo partner, WP4)."""
    if anonima is False and not pps.NOMINATIVO_DISPONIBILE:
        raise AppError(409, "nominativo_non_disponibile", MSG_NOMINATIVO_NON_DISPONIBILE)


async def _rpc(primary, nome: str, parametri: dict) -> Any:
    try:
        resp = await primary.rpc(nome, parametri).execute()
    except APIError as exc:
        _errore_rpc(exc)
    return resp.data


# ------------------------------------------------------------ letture DB


@dataclass
class Azienda:
    """L'azienda creatrice: riga, dati del registro, persone (solo per gli
    identificativi) e profilo partner."""

    company_id: str
    company: dict
    company_data: dict | None
    persone: list[dict]
    profilo_partner: dict | None

    @property
    def coerente(self) -> bool:
        """I dati del registro sono di QUESTA azienda (T5)."""
        piva = self.company.get("partita_iva")
        return bool(
            self.company_data and piva and self.company_data.get("piva_fetched") == piva
        )

    @property
    def dati_registro(self) -> dict | None:
        return self.company_data if self.coerente else None

    @property
    def dossier(self) -> dict:
        raw = (self.company_data or {}).get("raw")
        return build_dossier(raw) if self.coerente and isinstance(raw, dict) else {}

    @property
    def ident(self) -> Identificativi:
        return identificativi_azienda(self.company, self.company_data, self.persone)


async def _una(query) -> dict | None:
    resp = await query.limit(1).execute()
    return resp.data[0] if resp.data else None


async def carica_azienda(primary, company_id: str, owner_id: str | None, *,
                         viva: bool = True) -> Azienda:
    """Legge l'azienda (dell'owner, se indicato; viva, se richiesto) con
    dati del registro, persone e profilo partner. Assente → 404."""
    query = primary.table("company_profiles").select(AZIENDA_SELECT).eq("id", str(company_id))
    if owner_id is not None:
        query = query.eq("parent_id", str(owner_id))
    if viva:
        query = query.is_("deleted_at", "null").is_("archived_at", "null")
    company, dati, persone, profilo = await asyncio.gather(
        _una(query),
        _una(
            primary.table("company_data").select(COMPANY_DATA_SELECT)
            .eq("company_profile_id", str(company_id))
        ),
        primary.table("company_people").select(PERSONE_SELECT)
        .eq("company_profile_id", str(company_id)).execute(),
        _una(
            primary.table("company_partner_profiles").select(PROFILO_PARTNER_SELECT)
            .eq("company_profile_id", str(company_id))
        ),
    )
    if company is None:
        raise NotFoundError("Azienda non trovata")
    return Azienda(
        company_id=str(company_id),
        company=company,
        company_data=dati,
        persone=[p for p in (persone.data or []) if isinstance(p, dict)],
        profilo_partner=profilo,
    )


async def _requisiti(primary, call_id: str) -> list[dict]:
    resp = (
        await primary.table("partner_call_requisiti")
        .select(REQUISITO_SELECT)
        .eq("call_id", str(call_id))
        .order("ordine")
        .execute()
    )
    return sorted((r for r in resp.data or [] if isinstance(r, dict)),
                  key=lambda r: r.get("ordine") or 0)


async def _posizioni(primary, call_id: str) -> list[dict]:
    resp = (
        await primary.table("partner_call_posizioni")
        .select(POSIZIONE_SELECT)
        .eq("call_id", str(call_id))
        .order("ordine")
        .execute()
    )
    return sorted((p for p in resp.data or [] if isinstance(p, dict)),
                  key=lambda p: p.get("ordine") or 0)


async def _ricarica(primary, call_id: str) -> dict:
    riga = await _una(
        primary.table("partner_calls").select(CALL_SELECT).eq("id", str(call_id))
    )
    if riga is None:
        raise NotFoundError("Call di partenariato non trovata")
    return riga


async def _riga_partenariato(primary, bando_id: int) -> dict | None:
    """Estrazione WP3 del bando (solo `regole`, mai `extraction`)."""
    return await _una(
        primary.table("bando_partenariato").select(PARTENARIATO_SELECT)
        .eq("bando_id", int(bando_id))
    )


async def _lookups(secondary):
    """Lookup del catalogo (solo nomi): None se non disponibili."""
    try:
        return await lookup_service.get_lookups(secondary)
    except Exception as exc:  # noqa: BLE001 — servono solo ai nomi
        logger.warning("call: lookup del catalogo non disponibili (%s)", type(exc).__name__)
        return None


async def _stato_bando(secondary, bando_id: int):
    """(stato live da `bando_pubblico` o None se assente, letto?). Un errore
    di lettura NON è un'assenza: `letto=False`."""
    try:
        stati = await bando_fonti_service.leggi_stato_bandi(secondary, [int(bando_id)])
    except Exception as exc:  # noqa: BLE001
        logger.warning("call: stato del bando %s non leggibile (%s)", bando_id,
                       getattr(exc, "code", None) or type(exc).__name__)
        return None, False
    return stati.get(int(bando_id)), True


def _aperto(stato_bando) -> bool:
    effettivo = getattr(stato_bando, "stato_effettivo", None)
    return isinstance(effettivo, str) and effettivo.strip().lower() in STATI_BANDO_APERTI


async def _profilo_creatore(primary, az: Azienda) -> ProfiloCandidato:
    """Profilo deterministico del creatore per la vista «proprio». Il
    registro conta solo se è dell'azienda (T5); bilanci best-effort (senza,
    le regole finanziarie restano da verificare)."""
    try:
        esercizi, completo = await bilanci_service.carica_bilanci(primary, az.company_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("call: bilanci non leggibili (azienda %s, %s)", az.company_id,
                       type(exc).__name__)
        esercizi, completo = [], False
    return profilo_candidato_da(
        company=az.company,
        derived=((az.company_data or {}).get("derived") or {}) if az.coerente else None,
        dossier=az.dossier,
        profilo_partner=az.profilo_partner,
        esercizi=esercizi,
        storico_completo=completo,
    )


# ---------------------------------------------------- chiusure automatiche


def motivo_chiusura_auto(
    call: Mapping,
    *,
    stato_bando,
    bando_letto: bool,
    azienda_viva: bool,
    oggi: date,
) -> tuple[str, str] | None:
    """(nuovo stato, motivo) se la call va chiusa d'ufficio, altrimenti None.

    Solo bozze e pubblicate. Nell'ordine: azienda non viva → annullata;
    scadenza della call passata (solo pubblicate: una bozza non scade) →
    scaduta; bando revocato → annullata; chiuso o sospeso (Q18) → scaduta;
    assente da `bando_pubblico` da almeno 7 giorni (`bando_mancante_dal`) →
    annullata. Senza lettura del bando (`bando_letto=False`) i motivi del
    bando non si valutano: un errore non è un'assenza. Da bozza la RPC chiude
    sempre come annullata."""
    stato = call.get("stato")
    if stato not in STATI_MODIFICABILI:
        return None
    if not azienda_viva:
        return "chiusa_annullata", "azienda_non_disponibile"
    if stato == "pubblicata":
        scadenza = _data(call.get("scadenza_call"))
        if scadenza is not None and scadenza < oggi:
            return "scaduta", "scadenza_call"
    if not bando_letto:
        return None
    if stato_bando is None:
        mancante = _data(call.get("bando_mancante_dal"))
        if mancante is not None and (oggi - mancante).days >= GIORNI_BANDO_MANCANTE:
            return "chiusa_annullata", "bando_non_disponibile"
        return None
    effettivo = (getattr(stato_bando, "stato_effettivo", None) or "").strip().lower()
    if effettivo == "revocato":
        return "chiusa_annullata", "bando_revocato"
    if effettivo == "chiuso":
        return "scaduta", "bando_chiuso"
    if effettivo == "sospeso":
        return "scaduta", "bando_sospeso"
    return None


async def chiudi_automaticamente(primary, call: Mapping, nuovo_stato: str, motivo: str) -> bool:
    """Chiusura d'ufficio condizionata (`fn_partner_call_chiudi_auto`: solo da
    bozza o pubblicata) e, se è avvenuta ora, notifica al creatore e al
    titolare (dedup per call: un solo avviso anche se scheduler e lettura
    concorrono) e, per una call che era visibile a tutti, a chi l'ha salvata
    (WP6: chi non la vedeva più non riceve segnali); indice del matching
    invalidato."""
    visibile = pubblicamente_visibile(call)
    resp = await primary.rpc(
        "fn_partner_call_chiudi_auto",
        {"p_call": str(call["id"]), "p_nuovo_stato": nuovo_stato, "p_motivo": motivo},
    ).execute()
    if resp.data is not True:
        return False
    partenariato_indice.invalida()
    if visibile:
        await partenariato_notifiche.notifica_salvate(primary, call, "chiusa")
    destinatari = list(dict.fromkeys(
        str(u) for u in (call.get("family_parent_id"), call.get("creato_da")) if u
    ))
    company_id = str(call.get("company_profile_id") or "")
    await notify(
        primary,
        destinatari,
        tipo=TIPO_NOTIFICA_CHIUSURA,
        titolo="Una tua call di partenariato è stata chiusa",
        corpo=_CORPI_CHIUSURA.get(motivo),
        url=f"/app/partenariati/call/{call['id']}"
        + (f"?azienda={company_id}" if company_id else ""),
        dedup_key=f"call-chiusa:{call['id']}",
        company_profile_id=company_id or None,
    )
    return True


async def _controlla_in_lettura(primary, secondary, call: dict):
    """Controllo in lettura del dettaglio (oltre allo scheduler): scadenza
    della call e stato live del bando. Ritorna (call, stato bando, letto)."""
    if call.get("stato") not in STATI_MODIFICABILI:
        return call, None, False
    stato_bando, letto = await _stato_bando(secondary, call["bando_id"])
    esito = motivo_chiusura_auto(
        call, stato_bando=stato_bando, bando_letto=letto, azienda_viva=True, oggi=_oggi()
    )
    if esito is not None:
        try:
            if await chiudi_automaticamente(primary, call, *esito):
                call = await _ricarica(primary, call["id"])
        except Exception as exc:  # noqa: BLE001 — ci pensa lo scheduler
            logger.warning("call: chiusura in lettura non riuscita (call %s, %s)", call["id"],
                           type(exc).__name__)
    return call, stato_bando, letto


async def _failsafe_ai(primary, call: dict) -> dict:
    """Job AI `in_corso` oltre `partner_call_ai_stale_minuti` (processo
    riavviato): li chiude la RPC (errore `interrotta`, costo ignoto) e si
    rilegge. Best-effort."""
    minuti = get_settings().partner_call_ai_stale_minuti
    soglia = _adesso() - timedelta(minutes=minuti)
    orfano = False
    for prefisso in ("ai_posizioni", "ai_testi"):
        if call.get(f"{prefisso}_stato") == "in_corso":
            avviata = _ts(call.get(f"{prefisso}_avviata_at"))
            orfano = orfano or avviata is None or avviata <= soglia
    if not orfano:
        return call
    try:
        await primary.rpc("fn_partner_call_ai_chiudi_stale", {"p_minuti": minuti}).execute()
        return await _ricarica(primary, call["id"])
    except Exception:
        logger.exception("call: failsafe dei job AI non riuscito")
        return call


# ------------------------------------------------------------ composizione


def _costo_quota(call: Mapping):
    """Intervallo del costo della quota del creatore: budget ESATTO se c'è
    (vista «proprio»), altrimenti la fascia."""
    return intervallo_costo_quota(
        intervallo_budget(call.get("budget_fascia"), call.get("budget_progetto_eur")),
        call.get("quota_creatore_pct"),
    )


def _gap_requisiti(
    righe: list[dict], profilo: ProfiloCandidato, call: Mapping
) -> list[RequisitoOut]:
    """Requisiti SALVATI con la copertura del creatore ricalcolata ora (dati
    e budget correnti); etichette e ordine quelli salvati."""
    uscite = copertura_creatore(
        righe, profilo, _costo_quota(call), ruolo_creatore=call.get("ruolo_creatore")
    )
    return [
        out.model_copy(
            update={"etichetta": riga.get("etichetta"), "ordine": riga.get("ordine") or 0}
        )
        for out, riga in zip(uscite, righe, strict=True)
    ]


def _partenariato_gap(riga: Mapping | None) -> PartenariatoGapOut:
    if not riga:
        return PartenariatoGapOut(stato="non_estratta")
    esito = riga.get("esito")
    if riga.get("stato") == "in_corso" and not esito:
        stato = "in_corso"
    elif esito == "estratta":
        stato = "pronta"
    elif esito == "nessun_segnale":
        stato = "nessun_segnale"
    else:
        stato = "errore"
    modalita = riga.get("modalita_effettiva") if esito == "estratta" else None
    return PartenariatoGapOut(stato=stato, modalita_effettiva=modalita)


def _ai_check_gap(riga: Mapping | None) -> AiCheckGapOut:
    if not riga:
        return AiCheckGapOut(disponibile=False)
    return AiCheckGapOut(
        disponibile=True, id=riga.get("id"), data=riga.get("ready_at") or riga.get("created_at")
    )


def _non_ammesso(riga_bp: Mapping | None) -> bool:
    return bool(
        riga_bp
        and riga_bp.get("esito") == "estratta"
        and riga_bp.get("modalita_effettiva") == "non_ammesso"
    )


def snapshot_regole(call: Mapping) -> RegoleCallSnapshot | None:
    """Lo snapshot confermato, riletto con lo schema; uno snapshot che lo
    schema non accetta più vale come assente (e si logga)."""
    dato = call.get("regole_partenariato")
    if not isinstance(dato, dict):
        return None
    try:
        return RegoleCallSnapshot.model_validate(dato)
    except (ValidationError, AppError, ValueError):
        logger.warning("call: snapshot delle regole non leggibile (call %s)", call.get("id"))
        return None


def _posizioni_out(righe: list[dict]) -> list[PosizioneOut]:
    uscita = []
    for riga in righe:
        try:
            uscita.append(PosizioneOut.model_validate(riga))
        except ValidationError:
            logger.warning("call: posizione non leggibile (%s)", riga.get("id"))
    return uscita


def _job_posizioni(call: Mapping, requisiti_ids: set[str]) -> JobPosizioniOut:
    stato = call.get("ai_posizioni_stato") or "nessuno"
    proposta = None
    grezza = call.get("ai_posizioni_proposta")
    if stato == "pronta" and isinstance(grezza, dict):
        try:
            proposta = PropostaPosizioniOut.model_validate(grezza)
        except ValidationError:
            logger.warning("call: proposta di posizioni non leggibile (call %s)", call.get("id"))
        else:
            # I requisiti possono essere cambiati dopo la proposta: restano
            # solo i riferimenti ancora validi.
            for posizione in proposta.posizioni:
                posizione.requisiti_ids = [
                    i for i in posizione.requisiti_ids if str(i) in requisiti_ids
                ]
    return JobPosizioniOut(
        stato=stato,
        avviata_at=call.get("ai_posizioni_avviata_at"),
        errore=partner_call_ai.messaggio_errore(call.get("ai_posizioni_errore"))
        if stato == "errore" else None,
        proposta=proposta,
    )


def _job_testi(call: Mapping) -> JobTestiOut:
    stato = call.get("ai_testi_stato") or "nessuno"
    proposta = None
    grezza = call.get("ai_testi_proposta")
    if stato == "pronta" and isinstance(grezza, dict):
        try:
            proposta = PropostaTestiOut.model_validate(grezza)
        except ValidationError:
            logger.warning("call: proposta di testi non leggibile (call %s)", call.get("id"))
    return JobTestiOut(
        stato=stato,
        avviata_at=call.get("ai_testi_avviata_at"),
        errore=partner_call_ai.messaggio_errore(call.get("ai_testi_errore"))
        if stato == "errore" else None,
        proposta=proposta,
    )


# ---------------------------------------------------------- anti-contatti


@dataclass(frozen=True)
class _Testo:
    campo: str  # percorso per la UI («titolo», «posizioni.0.note», …)
    nome: str  # soggetto della frase («Il titolo», …)
    testo: Any


def _testi_pubblici(
    call: Mapping, requisiti: Iterable[Any], posizioni: Iterable[Any]
) -> list[_Testo]:
    """I testi che vedono i terzi: della call, delle posizioni e dei
    requisiti (etichette; testo se il requisito è visibile ai terzi — cercato
    o per ogni membro — qualunque sia l'origine, che la dichiara il client e
    il cui testo si può riscrivere; quello scritto a mano sempre)."""
    testi = [
        _Testo("titolo", "Il titolo", call.get("titolo")),
        _Testo("descrizione_pubblica", "La descrizione pubblica", call.get("descrizione_pubblica")),
        _Testo("profilo_partner_ideale", "Il profilo del partner ideale",
               call.get("profilo_partner_ideale")),
    ]
    for indice, posizione in enumerate(posizioni):
        titolo = _valore(posizione, "titolo")
        testi.append(_Testo(f"posizioni.{indice}.titolo", f"Il titolo della posizione {indice + 1}",
                            titolo))
        testi.append(_Testo(f"posizioni.{indice}.note", f"La nota della posizione {indice + 1}",
                            _valore(posizione, "note")))
    for indice, requisito in enumerate(requisiti):
        etichetta = _valore(requisito, "etichetta") or str(indice + 1)
        testi.append(_Testo(f"requisiti.{indice}.etichetta",
                            f"L'etichetta del requisito {etichetta}",
                            _valore(requisito, "etichetta")))
        visibile = requisito_visibile({"cercato": _valore(requisito, "cercato"),
                                       "ambito": _valore(requisito, "ambito")})
        if _valore(requisito, "origine") in (None, "manuale") or visibile:
            testi.append(_Testo(f"requisiti.{indice}.testo",
                                f"Il testo del requisito {etichetta}",
                                _valore(requisito, "testo")))
    return testi


def _valore(riga: Any, nome: str) -> Any:
    return riga.get(nome) if isinstance(riga, Mapping) else getattr(riga, nome, None)


def rilievi_pubblici(
    call: Mapping, requisiti: Iterable[Any], posizioni: Iterable[Any], ident: Identificativi | None
) -> list[RilievoOut]:
    """Rilievi anti-contatti sui testi pubblici (call anonima: anche gli
    identificativi dell'azienda). L'estratto va SOLO al creatore."""
    rilievi = []
    for voce in _testi_pubblici(call, requisiti, posizioni):
        for rilievo in rilievi_testo(voce.testo, ident):
            rilievi.append(RilievoOut(campo=voce.campo, tipo=rilievo.tipo,
                                      estratto=rilievo.estratto, bloccante=rilievo.bloccante))
    return rilievi


def _messaggio_non_conforme(nome: str, tipo: str) -> str:
    return (
        f"{nome} contiene {ETICHETTE_RILIEVO.get(tipo, 'un dato non ammesso')}: toglilo e salva "
        "di nuovo. Prima dell'accettazione non si condividono contatti né dati che "
        "identificano l'azienda"
    )


def _controlla(testi: Iterable[_Testo], ident: Identificativi | None) -> None:
    """Primo rilievo bloccante → 400 `testo_non_conforme` (nomina campo e
    tipo, MAI il dato)."""
    for voce in testi:
        for rilievo in rilievi_testo(voce.testo, ident):
            if rilievo.bloccante:
                raise AppError(400, "testo_non_conforme",
                               _messaggio_non_conforme(voce.nome, rilievo.tipo))


def _controlla_riservati(testo: Any) -> None:
    """I dettagli riservati possono nominare l'azienda, mai contatti diretti:
    si scambiano in chat dopo l'accettazione."""
    for rilievo in rilievi_testo(testo, None, anonima=False):
        if rilievo.bloccante and rilievo.tipo in _CONTATTI:
            raise AppError(
                400,
                "testo_non_conforme",
                f"I dettagli riservati contengono "
                f"{ETICHETTE_RILIEVO.get(rilievo.tipo, 'un contatto')}: i contatti si scambiano "
                "in chat dopo l'accettazione",
            )


# ------------------------------------------------------------ vista


def _identita_motivo(az: Azienda) -> str | None:
    """Stessa regola di `fn_partenariato_identita_ok` (T5), per la UI."""
    dati = az.company_data
    piva = az.company.get("partita_iva")
    if dati is None:
        return "dati_non_importati"
    if not piva or dati.get("piva_fetched") != piva:
        return "piva_diversa"
    if str(dati.get("stato_impresa") or "").strip().lower() != "attiva":
        return "impresa_non_attiva"
    if dati.get("sandbox") is not False and pps.richiedi_non_sandbox():
        return "dati_sandbox"
    return None


def _motivi_blocco(
    active,
    call: Mapping,
    az: Azienda,
    *,
    stato_bando,
    bando_letto: bool,
    riga_bp: Mapping | None,
    requisiti: list[dict],
    posizioni: list[dict],
    rilievi: list[RilievoOut],
    limiti,
) -> list[MotivoBloccoOut]:
    """Perché la bozza non si può pubblicare ORA (la RPC resta l'arbitro)."""
    motivi: list[MotivoBloccoOut] = []

    def blocca(codice: str, messaggio: str) -> None:
        motivi.append(MotivoBloccoOut(codice=codice, messaggio=messaggio))

    if not active.editable:
        blocca("solo_titolare", MSG_SOLO_TITOLARE)
    identita = _identita_motivo(az)
    if identita:
        blocca("identita_non_verificata", _MOTIVI_IDENTITA[identita])
    if bando_letto and not _aperto(stato_bando):
        blocca("bando_non_disponibile", RPC_ERRORS["bando_non_disponibile"][2])
    if _non_ammesso(riga_bp) and not call.get("override_non_ammesso_motivo"):
        blocca("partenariato_non_ammesso", MSG_NON_AMMESSO)
    if not (call.get("titolo") or "").strip():
        blocca("titolo_mancante", "Scrivi il titolo della call")
    if not (call.get("descrizione_pubblica") or "").strip():
        blocca("descrizione_mancante", "Scrivi la descrizione pubblica della call")
    if not call.get("regole_confermate_at"):
        blocca("regole_non_confermate", "Conferma le regole del bando")
    scadenza = _data(call.get("scadenza_call"))
    scadenza_bando = getattr(stato_bando, "data_scadenza", None) if bando_letto else None
    scadenza_bando = scadenza_bando or _data(call.get("bando_scadenza"))
    if scadenza is not None and (
        scadenza < _oggi() or (scadenza_bando is not None and scadenza > scadenza_bando)
    ):
        blocca("scadenza_call_non_valida", RPC_ERRORS["scadenza_call_non_valida"][2])
    if not posizioni:
        blocca("posizioni_mancanti", "Aggiungi almeno una posizione da cercare")
    if not any(r.get("cercato") is True for r in requisiti):
        blocca("nessun_requisito_cercato", "Indica almeno un requisito che cerchi nei partner")
    errori = errori_regole_finanziarie(requisiti, snapshot_regole(call))
    if errori:
        blocca("requisiti_non_validi", errori[0])
    if any(r.bloccante for r in rilievi):
        blocca(
            "testo_non_conforme",
            "Nei testi pubblici ci sono contatti o dati che identificano l'azienda: controlla "
            "l'anteprima",
        )
    call_attive = getattr(limiti, "call_attive", None)
    if call_attive is not None:
        if call_attive.limite == 0:
            blocca("piano_non_include_call", RPC_ERRORS["piano_non_include_call"][2])
        elif call_attive.residuo is not None and call_attive.residuo <= 0:
            blocca("limite_call_raggiunto", RPC_ERRORS["limite_call_raggiunto"][2])
    return motivi


def _bando_out(call: Mapping) -> BandoCallOut:
    return BandoCallOut(
        id=call["bando_id"],
        slug=call.get("bando_slug") or "",
        titolo=call.get("bando_titolo") or "",
        scadenza=call.get("bando_scadenza"),
        programma_id=call.get("bando_programma_id"),
        tipologia_id=call.get("bando_tipologia_id"),
        stato_effettivo=call.get("bando_stato_effettivo"),
        verificato_at=call.get("bando_verificato_at"),
        mancante_dal=call.get("bando_mancante_dal"),
    )


async def _gap_out(primary, call: Mapping, active, requisiti_out: list[RequisitoOut], *,
                   ai_row=None, riga_bp=None, letti: bool = False) -> GapOut:
    if not letti:
        ai_row = await ai_check_service.ultimo_ready(
            primary, owner_id=active.owner_id, company_id=call["company_profile_id"],
            bando_id=call["bando_id"],
        )
        riga_bp = await _riga_partenariato(primary, call["bando_id"])
    return GapOut(
        requisiti=requisiti_out,
        riepilogo=riepilogo(requisiti_out),
        ai_check=_ai_check_gap(ai_row),
        partenariato=_partenariato_gap(riga_bp),
    )


async def _vista(
    primary, active, call: dict, *, stato_bando=None, bando_letto: bool = False,
    az: Azienda | None = None, secondary=None,
) -> CallVistaCreatoreOut:
    """La call per l'azienda creatrice (titolare e membri con visibilità):
    tutto, compresi i riservati, più gap, job AI, limiti e motivi di blocco.
    Mai `family_parent_id` né `creato_da`. Per una bozza senza stato del
    bando già letto, lo legge (con `secondary`): i motivi di blocco devono
    dire il vero anche dopo una scrittura."""
    if call.get("stato") == "bozza" and not bando_letto and secondary is not None:
        stato_bando, bando_letto = await _stato_bando(secondary, call["bando_id"])
    az = az or await carica_azienda(primary, active.company_id, active.owner_id)
    requisiti, posizioni = await asyncio.gather(
        _requisiti(primary, call["id"]), _posizioni(primary, call["id"])
    )
    profilo = await _profilo_creatore(primary, az)
    ai_row = await ai_check_service.ultimo_ready(
        primary, owner_id=active.owner_id, company_id=az.company_id, bando_id=call["bando_id"]
    )
    riga_bp = await _riga_partenariato(primary, call["bando_id"])
    limiti = await entitlement_service.partenariati_for_owner(primary, active.owner_id)
    requisiti_out = _gap_requisiti(requisiti, profilo, call)
    gap = await _gap_out(primary, call, active, requisiti_out, ai_row=ai_row, riga_bp=riga_bp,
                         letti=True)
    motivi: list[MotivoBloccoOut] = []
    if call.get("stato") == "bozza":
        motivi = _motivi_blocco(
            active, call, az, stato_bando=stato_bando, bando_letto=bando_letto, riga_bp=riga_bp,
            requisiti=requisiti, posizioni=posizioni,
            rilievi=rilievi_pubblici(call, requisiti, posizioni, az.ident), limiti=limiti,
        )
    forma = call.get("forma_aggregazione_prevista")
    return CallVistaCreatoreOut(
        id=call["id"],
        company_profile_id=call["company_profile_id"],
        editable=bool(active.editable),
        stato=call["stato"],
        motivo_chiusura=call.get("motivo_chiusura"),
        versione=call.get("versione") or 0,
        wizard_passo=call.get("wizard_passo") or 1,
        bando=_bando_out(call),
        ruolo_creatore=call["ruolo_creatore"],
        forma_aggregazione_prevista=forma if forma != "altra" else None,
        anonima=call.get("anonima") is not False,
        titolo=call.get("titolo"),
        descrizione_pubblica=call.get("descrizione_pubblica"),
        dettagli_riservati=call.get("dettagli_riservati"),
        profilo_partner_ideale=call.get("profilo_partner_ideale"),
        budget_fascia=call.get("budget_fascia"),
        budget_progetto_eur=call.get("budget_progetto_eur"),
        quota_creatore_pct=call.get("quota_creatore_pct"),
        scadenza_call=call.get("scadenza_call"),
        visibilita=call.get("visibilita") or "pubblica",
        override_non_ammesso_motivo=call.get("override_non_ammesso_motivo"),
        regole_partenariato=snapshot_regole(call),
        regole_confermate_at=call.get("regole_confermate_at"),
        esclusivita=call.get("esclusivita") is True,
        posizioni=_posizioni_out(posizioni),
        gap=gap,
        ai_posizioni=_job_posizioni(call, {str(r.get("id")) for r in requisiti}),
        ai_testi=_job_testi(call),
        limiti=limiti,
        puo_pubblicare=call.get("stato") == "bozza" and not motivi,
        motivi_blocco=motivi,
        pubblicata_at=call.get("pubblicata_at"),
        chiusa_at=call.get("chiusa_at"),
        sospesa_at=call.get("sospesa_at"),
        sospeso_motivo=call.get("sospeso_motivo"),
        created_at=call.get("created_at"),
        updated_at=call.get("updated_at"),
    )


async def _carica_scrittura(primary, active, user: dict, call_id: Any) -> dict:
    """Titolare + azienda attiva + call dell'azienda attiva (404 altrimenti)."""
    _richiedi_titolare(active)
    _richiedi_azienda(active)
    call, _ = await carica_call_autorizzata(primary, call_id, active, user,
                                            ammessi=RUOLI_SCRITTURA)
    return call


def _parametri(active, user: dict, call: Mapping | None = None) -> dict:
    parametri = {
        "p_owner": str(active.owner_id),
        "p_company": str(active.company_id),
        "p_attore": str(user["id"]),
    }
    if call is not None:
        parametri["p_call"] = str(call["id"])
    return parametri


# ------------------------------------------------------------ letture


async def lista_mie(
    primary, secondary, active, user: dict, *, page: int = 1, page_size: int = 20
) -> Page[CallBachecaOut]:
    """Le call dell'azienda attiva (titolare e membri), dalla più recente,
    con i contatori della bacheca (senza match: sono le proprie)."""
    if not active.company_id:
        return Page.build([], 0, page, page_size)
    offset = (page - 1) * page_size
    resp = (
        await primary.table("partner_calls")
        .select(LISTA_SELECT, count="exact")
        .eq("company_profile_id", str(active.company_id))
        .eq("family_parent_id", str(active.owner_id))
        .order("created_at", desc=True)
        .range(offset, offset + page_size - 1)
        .execute()
    )
    calls = [c for c in resp.data or [] if isinstance(c, dict)]
    if not calls:
        return Page.build([], resp.count or 0, page, page_size)
    ids = [str(c["id"]) for c in calls]
    az = await carica_azienda(primary, active.company_id, active.owner_id)
    cercati, posizioni, lookups = await asyncio.gather(
        primary.table("partner_call_requisiti").select("call_id").in_("call_id", ids)
        .eq("cercato", True).execute(),
        primary.table("partner_call_posizioni").select("call_id,numero").in_("call_id", ids)
        .execute(),
        _lookups(secondary),
    )
    conta_cercati: dict[str, int] = {}
    for riga in cercati.data or []:
        conta_cercati[str(riga["call_id"])] = conta_cercati.get(str(riga["call_id"]), 0) + 1
    conta_posizioni: dict[str, int] = {}
    conta_posti: dict[str, int] = {}
    for riga in posizioni.data or []:
        chiave = str(riga["call_id"])
        conta_posizioni[chiave] = conta_posizioni.get(chiave, 0) + 1
        conta_posti[chiave] = conta_posti.get(chiave, 0) + _posti(riga)
    creatore = creatore_pubblico(az.dati_registro, az.dossier, nomi_regioni(lookups))
    ricevute = await candidature_ricevute(primary, ids)
    items = [
        call_bacheca(
            call_card(
                c, creatore, posizioni_n=conta_posizioni.get(str(c["id"]), 0),
                requisiti_cercati_n=conta_cercati.get(str(c["id"]), 0), mia=True,
                ident=az.ident,
            ),
            posti=conta_posti.get(str(c["id"]), 0),
            candidature_ricevute=ricevute.get(str(c["id"]), 0),
        )
        for c in calls
    ]
    return Page.build(items, resp.count or len(items), page, page_size)


# Candidature ricevute (WP8, prerequisito): spontanee, in attesa o accettate.
STATI_CANDIDATURE_RICEVUTE = ("inviata", "accettata")
_PAGINA_CONTEGGI = 1000


async def candidature_ricevute(primary, call_ids: Iterable[Any]) -> dict[str, int]:
    """Candidature SPONTANEE ricevute per call (`tipo = candidatura`, in
    attesa o accettate; gli inviti sono del creatore e non contano) per le
    card di UNA pagina: una lettura per blocco di 100 call (a keyset oltre le
    1000 righe, il max-rows di PostgREST), fuori dalla ricarica dell'indice
    (il suo budget di query non cambia). Best-effort: un errore vale 0 per
    tutte (le card non si rompono per un contatore)."""
    ids = sorted({str(c) for c in call_ids if c})
    conteggi: dict[str, int] = {}
    try:
        for inizio in range(0, len(ids), 100):
            blocco = ids[inizio : inizio + 100]
            ultimo = None
            while True:
                query = (
                    primary.table("partner_candidature").select("id,partner_call_id")
                    .in_("partner_call_id", blocco).eq("tipo", "candidatura")
                    .in_("stato", list(STATI_CANDIDATURE_RICEVUTE))
                )
                if ultimo is not None:
                    query = query.gt("id", ultimo)
                resp = await query.order("id").limit(_PAGINA_CONTEGGI).execute()
                righe = [r for r in resp.data or [] if isinstance(r, dict)]
                for riga in righe:
                    chiave = str(riga.get("partner_call_id"))
                    conteggi[chiave] = conteggi.get(chiave, 0) + 1
                if len(righe) < _PAGINA_CONTEGGI:
                    break
                ultimo = righe[-1]["id"]
    except Exception as exc:  # noqa: BLE001 — contatore informativo
        logger.warning("call: candidature ricevute non contate (%s)",
                       getattr(exc, "code", None) or type(exc).__name__)
        return {}
    return conteggi


def _posti(posizione: Mapping) -> int:
    """Partner cercati da una posizione (`numero`, almeno 1)."""
    numero = posizione.get("numero")
    return numero if isinstance(numero, int) and not isinstance(numero, bool) and numero > 0 else 1


async def dettaglio(
    primary, secondary, active, user: dict, call_id: Any
) -> CallVistaCreatoreOut | CallPubblicaDettaglioOut | CallVistaControparteOut:
    """GET della call. Per l'azienda creatrice (titolare e membri) la vista
    completa, con il failsafe dei job AI e il controllo in lettura delle
    chiusure automatiche; per la controparte accettata (WP7) la vista
    controparte; per le altre aziende (WP6) la vista PUBBLICA con il proprio
    match e, per chi si è candidato o è stato invitato, la propria
    candidatura (`_dettaglio_pubblico`)."""
    call, ruolo = await carica_call_autorizzata(primary, call_id, active, user)
    if ruolo == "controparte":
        return await _vista_controparte(primary, secondary, active, call)
    if ruolo not in RUOLI_AZIENDA:
        return await _dettaglio_pubblico(primary, secondary, active, call, ruolo=ruolo)
    call = await _failsafe_ai(primary, call)
    call, stato_bando, letto = await _controlla_in_lettura(primary, secondary, call)
    return await _vista(primary, active, call, stato_bando=stato_bando, bando_letto=letto)


async def versioni(primary, secondary, active, user: dict, call_id: Any) -> list[VersioneOut]:
    """Versioni pubblicate della call (solo l'azienda creatrice), a whitelist."""
    call, _ = await carica_call_autorizzata(primary, call_id, active, user, ammessi=RUOLI_AZIENDA)
    resp = (
        await primary.table("partner_call_versioni")
        .select("versione,created_at,snapshot")
        .eq("call_id", str(call["id"]))
        .order("versione", desc=True)
        .execute()
    )
    return [
        VersioneOut(
            versione=riga["versione"],
            created_at=riga["created_at"],
            snapshot=proietta_versione(riga.get("snapshot")),
        )
        for riga in sorted(resp.data or [], key=lambda r: -int(r.get("versione") or 0))
    ]


async def _proiezione_pubblica(
    primary, secondary, call: Mapping, az: Azienda | None = None, *,
    requisiti: list[dict] | None = None, posizioni: list[dict] | None = None,
) -> CallPubblicaOut:
    """La call come la vedono i terzi (anteprima e segnalazioni)."""
    if az is None:
        az = await carica_azienda(primary, call["company_profile_id"], call["family_parent_id"],
                                  viva=False)
    if requisiti is None or posizioni is None:
        requisiti, posizioni = await asyncio.gather(
            _requisiti(primary, call["id"]), _posizioni(primary, call["id"])
        )
    regioni = nomi_regioni(await _lookups(secondary))
    creatore = creatore_pubblico(az.dati_registro, az.dossier, regioni)
    return call_pubblica(call, requisiti, posizioni, creatore, ident=az.ident, regioni=regioni)


async def anteprima(primary, secondary, active, user: dict, call_id: Any) -> AnteprimaOut:
    """«Come ti vedono»: la proiezione pubblica (anche di una bozza) e i
    rilievi anti-contatti dei testi pubblici (estratti solo per il creatore)."""
    call, _ = await carica_call_autorizzata(primary, call_id, active, user, ammessi=RUOLI_AZIENDA)
    az = await carica_azienda(primary, active.company_id, active.owner_id)
    requisiti, posizioni = await asyncio.gather(
        _requisiti(primary, call["id"]), _posizioni(primary, call["id"])
    )
    return AnteprimaOut(
        call=await _proiezione_pubblica(primary, secondary, call, az, requisiti=requisiti,
                                        posizioni=posizioni),
        rilievi=rilievi_pubblici(call, requisiti, posizioni, az.ident),
    )


# ------------------------------------------------------------ scritture


async def _bando_catalogo(secondary, slug: str) -> dict:
    resp = (
        await secondary.table("bando")
        .select(BANDO_CATALOGO_SELECT)
        .eq("slug", slug)
        .eq("stato_processing", "completed")
        .limit(1)
        .execute()
    )
    if not resp.data:
        raise NotFoundError("Bando non trovato")
    return resp.data[0]


def _id_embed(valore: Any) -> int | None:
    if isinstance(valore, list):
        valore = valore[0] if valore else None
    id_ = valore.get("id") if isinstance(valore, Mapping) else None
    return id_ if isinstance(id_, int) and not isinstance(id_, bool) else None


def _ref_partenariato(riga_bp: Mapping | None) -> dict | None:
    if not riga_bp or riga_bp.get("esito") != "estratta":
        return None
    return {
        "prompt_version": riga_bp.get("prompt_version"),
        "estratta_at": riga_bp.get("estratta_at"),
        "modalita_effettiva": riga_bp.get("modalita_effettiva"),
    }


async def crea_bozza(primary, secondary, active, user: dict, dati: CallCreaIn
                     ) -> CallVistaCreatoreOut:
    """Nuova bozza sull'azienda attiva (titolare). Errori: 403, 404 (bando o
    azienda), 409 `nominativo_non_disponibile` / `bando_non_disponibile` /
    `partenariato_non_ammesso` / `call_gia_presente` / `troppe_bozze`, 403
    `piano_non_include_call`."""
    _richiedi_titolare(active)
    _richiedi_azienda(active)
    _nominativo(dati.anonima)
    bando = await _bando_catalogo(secondary, dati.bando_slug)
    bando_id = int(bando["id"])
    stato_bando, letto = await _stato_bando(secondary, bando_id)
    if not letto:
        raise UpstreamError(MSG_BANDO_NON_VERIFICABILE)
    if not _aperto(stato_bando):
        raise _errore_mappa("bando_non_disponibile")
    riga_bp = await _riga_partenariato(primary, bando_id)
    if _non_ammesso(riga_bp) and not dati.override_non_ammesso_motivo:
        raise AppError(409, "partenariato_non_ammesso", MSG_NON_AMMESSO)

    scadenza = stato_bando.data_scadenza
    p_bando = {
        "id": bando_id,
        "slug": bando.get("slug") or dati.bando_slug,
        "titolo": (bando.get("titolo") or bando.get("titolo_breve") or dati.bando_slug).strip(),
        "scadenza": scadenza.isoformat() if scadenza else None,
        "programma_id": _id_embed(bando.get("programmi")),
        "tipologia_id": _id_embed(bando.get("tipologie_bando")),
        "stato_effettivo": stato_bando.stato_effettivo,
    }
    p_dati = {
        "ruolo_creatore": dati.ruolo_creatore,
        "forma_aggregazione_prevista": dati.forma_aggregazione_prevista,
        "anonima": True,
        "override_non_ammesso_motivo": dati.override_non_ammesso_motivo,
        "partenariato_ref": _ref_partenariato(riga_bp),
        # Il passo 1 (bando) si salva creando la bozza: si riprende dal 2.
        "wizard_passo": 2,
    }
    riga = await _rpc(primary, "fn_partner_call_crea_bozza", {
        **_parametri(active, user),
        "p_bando": p_bando,
        "p_dati": p_dati,
        "p_max_bozze": get_settings().partner_call_bozze_max,
    })
    if not isinstance(riga, dict) or not riga.get("id"):
        raise UpstreamError()
    call = await _ricarica(primary, riga["id"])
    return await _vista(primary, active, call, stato_bando=stato_bando, bando_letto=True)


async def aggiorna(primary, secondary, active, user: dict, call_id: Any, dati: CallAggiornaIn
                   ) -> CallVistaCreatoreOut:
    """PATCH parziale (solo i campi inviati). Testi pubblici senza contatti né
    identificativi, riservati senza contatti; budget esatto dentro la fascia
    (anche quella già salvata). In `pubblicata` la RPC ammette solo la
    whitelist e crea una nuova versione."""
    call = await _carica_scrittura(primary, active, user, call_id)
    campi = dati.campi()
    _nominativo(campi.get("anonima"))
    if not campi:
        return await _vista(primary, active, call, secondary=secondary)
    az = await carica_azienda(primary, active.company_id, active.owner_id)
    _controlla(
        [voce for voce in _testi_pubblici(campi, (), ()) if voce.campo in campi], az.ident
    )
    if "dettagli_riservati" in campi:
        _controlla_riservati(campi["dettagli_riservati"])
    fascia = campi["budget_fascia"] if "budget_fascia" in campi else call.get("budget_fascia")
    budget = _decimale(
        campi["budget_progetto_eur"] if "budget_progetto_eur" in campi
        else call.get("budget_progetto_eur")
    )
    if fascia and budget is not None and not budget_nella_fascia(fascia, budget):
        raise BadRequestError("Il budget del progetto non rientra nella fascia scelta")
    await _rpc(primary, "fn_partner_call_aggiorna", {**_parametri(active, user, call),
                                                     "p_campi": campi})
    aggiornata = await _ricarica(primary, call["id"])
    await _dopo_modifica(primary, call, aggiornata)
    return await _vista(primary, active, aggiornata, az=az, secondary=secondary)


async def aggiorna_budget(primary, active, user: dict, call: Mapping, campi: dict) -> dict:
    """Budget della call dal consorzio (WP8: `budget_fascia` pubblica e
    `budget_progetto_eur` RISERVATO, già validati da `BudgetIn`) con la
    stessa RPC e gli stessi effetti del PATCH: whitelist e versione nella RPC
    (in `chiusa_completata` → 409 `stato_call_non_valido`), indice
    invalidato, «modificata» a chi segue la call solo se cambia la
    proiezione pubblica. La call l'ha già caricata il chiamante come
    creatore (titolare). Ritorna la call riletta."""
    await _rpc(primary, "fn_partner_call_aggiorna", {**_parametri(active, user, call),
                                                     "p_campi": campi})
    aggiornata = await _ricarica(primary, call["id"])
    await _dopo_modifica(primary, call, aggiornata)
    return aggiornata


async def _dopo_modifica(primary, prima: Mapping, dopo: Mapping) -> None:
    """Dopo una scrittura riuscita su una call PUBBLICATA: indice del
    matching invalidato e, solo se chi la segue può accorgersi della modifica,
    notifica «modificata» (WP6). Niente notifica se la RPC non ha creato una
    nuova versione (salvataggio senza modifiche), se la call non è visibile a
    tutti prima e dopo (solo su invito o sospesa: chi la segue non la vede)
    o se la proiezione pubblica non è cambiata (modifiche dei soli campi
    riservati o dei requisiti che i terzi non vedono): la notifica non deve
    rivelare ciò che la call nasconde. Best-effort."""
    if prima.get("stato") != "pubblicata":
        return
    partenariato_indice.invalida()
    if dopo.get("versione") == prima.get("versione"):
        return
    if not (pubblicamente_visibile(prima) and pubblicamente_visibile(dopo)):
        return
    if not await _proiezione_cambiata(
        primary, dopo["id"], prima.get("versione"), dopo.get("versione")
    ):
        return
    await partenariato_notifiche.notifica_salvate(primary, dopo, "modificata")


def _pubblica_da_versione(snapshot: Any) -> dict | None:
    """La proiezione pubblica (`call_pubblica`, la stessa verso terzi) dello
    snapshot di una versione; il creatore è lo stesso nelle due versioni e
    non entra nel confronto."""
    snapshot = snapshot if isinstance(snapshot, Mapping) else {}
    call = snapshot.get("call")
    if not isinstance(call, Mapping) or call.get("id") is None:
        return None
    try:
        return call_pubblica(
            call,
            [r for r in snapshot.get("requisiti") or [] if isinstance(r, Mapping)],
            [p for p in snapshot.get("posizioni") or [] if isinstance(p, Mapping)],
            creatore_pubblico(None, None, {}),
            ident=None,
            regioni={},
        ).model_dump(mode="json")
    except (KeyError, TypeError, ValueError):
        return None


async def _proiezione_cambiata(primary, call_id: Any, prima: Any, dopo: Any) -> bool:
    """La proiezione pubblica è cambiata tra le versioni `prima` e `dopo`
    (snapshot di `partner_call_versioni`)? Nel dubbio (snapshot mancanti o
    illeggibili) no: meglio una notifica in meno che un segnale su un dato
    riservato."""
    try:
        resp = await (
            primary.table("partner_call_versioni").select("versione,snapshot")
            .eq("call_id", str(call_id)).in_("versione", [prima, dopo]).execute()
        )
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("call: versioni non lette per la notifica (call %s, %s)", call_id,
                       getattr(exc, "code", None) or type(exc).__name__)
        return False
    snapshot = {r.get("versione"): r.get("snapshot") for r in resp.data or []}
    vecchia, nuova = (_pubblica_da_versione(snapshot.get(v)) for v in (prima, dopo))
    return vecchia is not None and nuova is not None and vecchia != nuova


async def conferma_regole(primary, secondary, active, user: dict, call_id: Any,
                          dati: RegoleConfermaIn) -> CallVistaCreatoreOut:
    """Conferma lo snapshot delle regole (solo bozza). Le voci `confermata`
    devono coincidere con voci VERIFICATE dell'estrazione WP3 corrente;
    `fonte` la scrive il servizio (quella del client si ignora)."""
    call = await _carica_scrittura(primary, active, user, call_id)
    if call.get("stato") != "bozza":
        raise AppError(409, "stato_call_non_valido",
                       "Le regole si confermano solo prima della pubblicazione")
    riga_bp = await _riga_partenariato(primary, call["bando_id"])
    estratte = (riga_bp or {}).get("regole") if (riga_bp or {}).get("esito") == "estratta" else None
    errori = errori_voci_confermate(dati.regole, estratte)
    if errori:
        raise BadRequestError(errori[0])
    fonte = None
    if riga_bp and riga_bp.get("esito") == "estratta" and riga_bp.get("modalita_effettiva"):
        fonte = FonteSnapshot(
            estratta_at=riga_bp.get("estratta_at"),
            prompt_version=riga_bp.get("prompt_version"),
            modalita_effettiva=riga_bp["modalita_effettiva"],
        )
    snapshot = dati.regole.model_copy(update={"fonte": fonte})
    await _rpc(primary, "fn_partner_call_conferma_regole", {
        **_parametri(active, user, call),
        "p_regole": snapshot.model_dump(mode="json"),
        "p_esclusivita": dati.esclusivita,
    })
    return await _vista(primary, active, await _ricarica(primary, call["id"]),
                        secondary=secondary)


async def _bando_facet(secondary, slug: str) -> dict | None:
    """Facet del catalogo per i pre-check (id e nomi); None se il bando non
    si legge più (la gap analysis procede con le altre fonti)."""
    try:
        resp = (
            await secondary.table("bando")
            .select(BANDO_FACET_SELECT)
            .eq("slug", slug)
            .eq("stato_processing", "completed")
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("call: facet del bando non leggibili (%s)", type(exc).__name__)
        return None
    return resp.data[0] if resp.data else None


def _regioni_bando(bando: Mapping | None) -> list[int]:
    ids = []
    for riga in (bando or {}).get("bando_regioni") or []:
        regione = riga.get("regioni") if isinstance(riga, Mapping) else None
        id_ = regione.get("id") if isinstance(regione, Mapping) else None
        if isinstance(id_, int) and not isinstance(id_, bool):
            ids.append(id_)
    return list(dict.fromkeys(ids))


def _etichette_provvisorie(requisiti: list[RequisitoOut], salvate: Mapping[str, str]
                           ) -> list[RequisitoOut]:
    """Etichette come le assegnerà la RPC: i requisiti conservati tengono la
    loro, i nuovi prendono le prime libere in ordine («A», «B», …). Solo per
    mostrarle: al salvataggio l'etichetta di un requisito nuovo non si
    manda (`salva_requisiti`)."""
    usate = {salvate[str(r.id)] for r in requisiti if r.id is not None and str(r.id) in salvate}
    auto = 0
    uscita = []
    for requisito in requisiti:
        if requisito.id is not None and str(requisito.id) in salvate:
            etichetta = salvate[str(requisito.id)]
        else:
            while True:
                etichetta = etichetta_breve(auto)
                auto += 1
                if etichetta not in usate:
                    break
            usate.add(etichetta)
        uscita.append(requisito.model_copy(update={"etichetta": etichetta}))
    return uscita


async def genera_requisiti(primary, secondary, active, user: dict, call_id: Any) -> GapOut:
    """PROPOSTA di requisiti (non salvata): regole confermate → pre-check del
    catalogo → ultimo AI-check `ready` (fonte più affidabile per prima),
    deduplicati, con la copertura del creatore. I requisiti già salvati con
    la stessa origine e riferimento (con l'impronta del contenuto: una voce
    rinumerata non eredita niente) conservano id, etichetta e «cercato»; i
    requisiti scritti a mano restano in coda; gli altri salvati che la
    proposta non ripropone escono al salvataggio. Le etichette dei nuovi sono
    provvisorie (`_etichette_provvisorie`)."""
    call = await _carica_scrittura(primary, active, user, call_id)
    if call.get("stato") not in STATI_MODIFICABILI:
        raise _errore_mappa("stato_call_non_valido")
    az = await carica_azienda(primary, active.company_id, active.owner_id)
    bando, ai_row, riga_bp, salvati = await asyncio.gather(
        _bando_facet(secondary, call["bando_slug"]),
        ai_check_service.ultimo_ready(
            primary, owner_id=active.owner_id, company_id=az.company_id,
            bando_id=call["bando_id"],
        ),
        _riga_partenariato(primary, call["bando_id"]),
        _requisiti(primary, call["id"]),
    )
    snapshot = snapshot_regole(call)
    bozze = unisci(
        da_regole=requisiti_da_regole(snapshot, regioni_bando=_regioni_bando(bando))
        if snapshot else [],
        da_precheck=requisiti_da_precheck(None, bando=bando) if bando else [],
        da_ai_check=requisiti_da_ai_check((ai_row or {}).get("report")),
    )
    per_riferimento = {
        (r.get("origine"), r.get("rif_origine")): r for r in salvati if r.get("rif_origine")
    }
    usati: set = set()
    for bozza in bozze:
        chiave = (bozza.origine, bozza.rif_origine)
        salvato = per_riferimento.get(chiave)
        if salvato is not None and chiave not in usati:
            usati.add(chiave)
            bozza.id = uuid.UUID(str(salvato["id"]))
            bozza.cercato = salvato.get("cercato") is True
    manuali = [r for r in salvati if r.get("origine") == "manuale"]
    profilo = await _profilo_creatore(primary, az)
    proposti = copertura_creatore(
        [*bozze, *manuali], profilo, _costo_quota(call), ruolo_creatore=call["ruolo_creatore"]
    )
    proposti = _etichette_provvisorie(
        proposti, {str(r["id"]): r["etichetta"] for r in salvati if r.get("etichetta")}
    )
    nuovo_ai_check = ai_row and str(call.get("ai_check_id")) != str(ai_row["id"])
    if call.get("stato") == "bozza" and nuovo_ai_check:
        try:
            await primary.rpc("fn_partner_call_aggiorna", {
                **_parametri(active, user, call), "p_campi": {"ai_check_id": str(ai_row["id"])}
            }).execute()
        except Exception as exc:  # noqa: BLE001 — solo tracciabilità
            logger.warning("call: ai_check_id non aggiornato (call %s, %s)", call["id"],
                           type(exc).__name__)
    return await _gap_out(primary, call, active, proposti, ai_row=ai_row, riga_bp=riga_bp,
                          letti=True)


async def salva_requisiti(primary, secondary, active, user: dict, call_id: Any,
                          dati: RequisitiIn) -> GapOut:
    """Replace-all dei requisiti con la copertura del creatore calcolata ora
    (solo template, mai testo del modello). Etichette e testi scritti a mano
    senza contatti né identificativi; regole finanziarie solo identiche a
    quelle confermate (Q11). Evidenze dell'ULTIMO AI-check: un requisito
    dall'AI-check conserva il verdetto sul suo riferimento (che porta
    l'impronta del testo: un'altra estrazione con lo stesso «R1» non vale);
    un requisito tipizzato riceve i verdetti delle voci che assorbe, come
    nella proposta. L'etichetta si rispetta solo per i requisiti conservati
    (con id): ai nuovi la assegna la RPC, senza spostare su di loro i
    collegamenti delle posizioni a un requisito rimosso."""
    call = await _carica_scrittura(primary, active, user, call_id)
    az = await carica_azienda(primary, active.company_id, active.owner_id)
    _controlla(
        [v for v in _testi_pubblici({}, dati.requisiti, ()) if v.campo.startswith("requisiti.")],
        az.ident,
    )
    errori = errori_regole_finanziarie(dati.requisiti, snapshot_regole(call))
    if errori:
        raise BadRequestError(errori[0])
    evidenze: dict[str, tuple[str, ...]] = {}
    da_ai_check: list[RequisitoBozza] = []
    if any(r.origine != "manuale" for r in dati.requisiti):
        ai_row = await ai_check_service.ultimo_ready(
            primary, owner_id=active.owner_id, company_id=az.company_id,
            bando_id=call["bando_id"],
        )
        da_ai_check = requisiti_da_ai_check((ai_row or {}).get("report"))
        evidenze = {b.rif_origine: b.esiti_ai_check for b in da_ai_check if b.rif_origine}
    bozze = [
        RequisitoBozza(
            origine=r.origine,
            testo=r.testo,
            criterio=r.criterio,
            ambito=r.ambito,
            rif_origine=r.rif_origine,
            citazione=r.citazione.model_dump(mode="json") if r.citazione else None,
            esiti_ai_check=evidenze.get(r.rif_origine or "", ()) if r.origine == "ai_check"
            else (),
            cercato=r.cercato,
            id=r.id,
        )
        for r in dati.requisiti
    ]
    propri = {r.rif_origine for r in dati.requisiti if r.origine == "ai_check" and r.rif_origine}
    evidenze_assorbite(bozze, [b for b in da_ai_check if b.rif_origine not in propri])
    profilo = await _profilo_creatore(primary, az)
    uscite = copertura_creatore(bozze, profilo, _costo_quota(call),
                                ruolo_creatore=call["ruolo_creatore"])
    payload = []
    for richiesto, out in zip(dati.requisiti, uscite, strict=True):
        elemento = payload_requisito(out)
        if richiesto.etichetta and richiesto.id is not None:
            elemento["etichetta"] = richiesto.etichetta
        payload.append(elemento)
    await _rpc(primary, "fn_partner_call_sostituisci_requisiti", {
        **_parametri(active, user, call), "p_requisiti": payload,
    })
    aggiornata = await _ricarica(primary, call["id"])
    await _dopo_modifica(primary, call, aggiornata)
    call = aggiornata
    righe = await _requisiti(primary, call["id"])
    return await _gap_out(primary, call, active, _gap_requisiti(righe, profilo, call))


async def salva_posizioni(primary, secondary, active, user: dict, call_id: Any,
                          dati: PosizioniIn) -> CallVistaCreatoreOut:
    """Replace-all delle posizioni: titoli e note senza contatti né
    identificativi, regioni della lookup del catalogo (fail-closed), requisiti
    della stessa call (lo verifica la RPC)."""
    call = await _carica_scrittura(primary, active, user, call_id)
    az = await carica_azienda(primary, active.company_id, active.owner_id)
    _controlla(
        [v for v in _testi_pubblici({}, (), dati.posizioni) if v.campo.startswith("posizioni.")],
        az.ident,
    )
    regioni = {i for p in dati.posizioni for i in p.regioni}
    if regioni:
        # Fail-closed: senza catalogo gli id non si verificano e non si salva.
        ammesse = set(nomi_regioni(await lookup_service.get_lookups(secondary)))
        if not regioni <= ammesse:
            raise BadRequestError("Regione non riconosciuta")
    payload = []
    for posizione in dati.posizioni:
        elemento = posizione.model_dump(mode="json")
        if elemento.get("id") is None:
            elemento.pop("id", None)
        payload.append(elemento)
    await _rpc(primary, "fn_partner_call_sostituisci_posizioni", {
        **_parametri(active, user, call), "p_posizioni": payload,
    })
    aggiornata = await _ricarica(primary, call["id"])
    await _dopo_modifica(primary, call, aggiornata)
    return await _vista(primary, active, aggiornata, az=az, secondary=secondary)


async def pubblica(primary, secondary, active, user: dict, call_id: Any,
                   scadenza_call: date | None = None) -> CallVistaCreatoreOut:
    """Pubblica la bozza. Controlli in Python prima della RPC: stato LIVE del
    bando, `non_ammesso` senza motivo, rilievi bloccanti nei testi pubblici,
    regole finanziarie dei requisiti ancora nello snapshot, scadenza di
    default `min(scadenza del bando, oggi + N giorni)`. La RPC ricontrolla
    tutto sotto i lock e applica identità e limiti del piano."""
    call = await _carica_scrittura(primary, active, user, call_id)
    if call.get("stato") != "bozza":
        raise AppError(409, "stato_call_non_valido", "Si può pubblicare solo una bozza")
    stato_bando, letto = await _stato_bando(secondary, call["bando_id"])
    if not letto:
        raise UpstreamError(MSG_BANDO_NON_VERIFICABILE)
    if not _aperto(stato_bando):
        raise _errore_mappa("bando_non_disponibile")
    riga_bp = await _riga_partenariato(primary, call["bando_id"])
    if _non_ammesso(riga_bp) and not call.get("override_non_ammesso_motivo"):
        raise AppError(409, "partenariato_non_ammesso", MSG_NON_AMMESSO)
    az = await carica_azienda(primary, active.company_id, active.owner_id)
    requisiti, posizioni = await asyncio.gather(
        _requisiti(primary, call["id"]), _posizioni(primary, call["id"])
    )
    _controlla(_testi_pubblici(call, requisiti, posizioni), az.ident)
    errori = errori_regole_finanziarie(requisiti, snapshot_regole(call))
    if errori:
        raise BadRequestError(errori[0])
    scadenza = scadenza_call or _data(call.get("scadenza_call"))
    if scadenza is None:
        scadenza = _oggi() + timedelta(days=get_settings().partner_call_scadenza_default_giorni)
        if stato_bando.data_scadenza is not None:
            scadenza = min(scadenza, stato_bando.data_scadenza)
    await _rpc(primary, "fn_partner_call_pubblica", {
        **_parametri(active, user, call),
        "p_bando_stato": stato_bando.stato_effettivo,
        "p_bando_scadenza": stato_bando.data_scadenza.isoformat()
        if stato_bando.data_scadenza else None,
        "p_scadenza_call": scadenza.isoformat(),
        "p_richiedi_non_sandbox": pps.richiedi_non_sandbox(),
    })
    # WP6: la call entra subito nell'indice; in background le chiavi dei
    # collegamenti del creatore e il fan-out delle notifiche proattive
    # (ripreso dallo scheduler se il processo muore a metà).
    partenariato_indice.invalida()
    _spawn(partenariato_notifiche.dopo_pubblicazione(
        primary, secondary, str(call["id"]), str(active.company_id)
    ))
    call = await _ricarica(primary, call["id"])
    return await _vista(primary, active, call, stato_bando=stato_bando, bando_letto=True, az=az)


async def chiudi(primary, secondary, active, user: dict, call_id: Any, dati: ChiudiIn
                 ) -> CallVistaCreatoreOut:
    """Chiusura dal creatore: completata (solo da pubblicata) o annullata."""
    call = await _carica_scrittura(primary, active, user, call_id)
    await _rpc(primary, "fn_partner_call_chiudi", {
        **_parametri(active, user, call), "p_esito": dati.esito,
    })
    chiusa = await _ricarica(primary, call["id"])
    if call.get("stato") == "pubblicata":
        partenariato_indice.invalida()
        # Solo a chi la vedeva: una call solo su invito o sospesa è già 404
        # per chi la segue, e la notifica ne rivelerebbe l'esistenza.
        if pubblicamente_visibile(call):
            await partenariato_notifiche.notifica_salvate(primary, chiusa, "chiusa")
    return await _vista(primary, active, chiusa)


# ------------------------------------------------------------ proposte AI


async def _avvia_job(primary, secondary, ai, active, user: dict, call_id: Any, servizio: str
                     ) -> datetime:
    """Prenota (fail-closed) e avvia in background una proposta AI. Errori:
    403, 404, 409 `stato_call_non_valido` / `ai_in_corso`, 503
    `ai_not_configured`, 429 `ai_limite_giornaliero` / `ai_sospesa_oggi`."""
    call = await _carica_scrittura(primary, active, user, call_id)
    if call.get("stato") != "bozza":
        raise AppError(409, "stato_call_non_valido",
                       "Le proposte automatiche servono solo prima della pubblicazione")
    if not ai.enabled:
        raise AiNotConfiguredError(MSG_AI_NON_CONFIGURATA)
    az = await carica_azienda(primary, active.company_id, active.owner_id)
    requisiti, posizioni, lookups = await asyncio.gather(
        _requisiti(primary, call["id"]), _posizioni(primary, call["id"]), _lookups(secondary)
    )
    regioni = nomi_regioni(lookups)
    creatore = creatore_pubblico(az.dati_registro, az.dossier, regioni)
    snapshot = snapshot_regole(call)
    ident = az.ident
    if servizio == partner_call_ai.SERVIZIO_POSIZIONI:
        system, schema, schema_testo = SYSTEM_POSIZIONI, PropostaPosizioni, schema_posizioni_json()
        versione = POSIZIONI_PROMPT_VERSION
        messaggio = build_posizioni_input(
            call=call, creatore=creatore,
            competenze_creatore=(az.profilo_partner or {}).get("competenze") or [],
            regole=snapshot, requisiti=requisiti, regioni=list(regioni.values()), ident=ident,
            persone=az.persone,
        )
        post = functools.partial(
            partner_call_ai.post_posizioni,
            etichette={str(r["etichetta"]): str(r["id"]) for r in requisiti if r.get("etichetta")},
            regioni=regioni, ident=ident, ruolo_creatore=call.get("ruolo_creatore"),
            quota_creatore=call.get("quota_creatore_pct"), regole=snapshot,
        )
    else:
        system, schema, schema_testo = SYSTEM_TESTI, BozzaTestiCall, schema_testi_json()
        versione = TESTI_PROMPT_VERSION
        messaggio = build_testi_input(
            call=call, creatore=creatore, requisiti=requisiti, posizioni=posizioni,
            regioni=regioni, ident=ident, persone=az.persone,
        )
        post = functools.partial(partner_call_ai.post_testi, ident=ident)

    settings = get_settings()
    riserva = partner_call_ai.stima_riserva_cents(system, messaggio, schema_testo)
    esecuzione_id = await _rpc(primary, "fn_partner_call_ai_prenota", {
        "p_owner": str(active.owner_id),
        "p_company": str(active.company_id),
        "p_call": str(call["id"]),
        "p_richiedente": str(user["id"]),
        "p_servizio": servizio,
        "p_budget_cents": budget_cents_gruppo("altri"),
        "p_costo_riservato_cents": riserva,
        "p_limite_call": settings.partner_call_ai_limite_giorno,
        "p_limite_owner": settings.partner_call_ai_limite_owner_giorno,
    })
    if not isinstance(esecuzione_id, str) or not esecuzione_id:
        # Senza un'esecuzione registrata la spesa non sarebbe contata.
        raise UpstreamError()
    _spawn(partner_call_ai.esegui_job(primary, ai, partner_call_ai.RichiestaJob(
        servizio=servizio, call_id=str(call["id"]), esecuzione_id=esecuzione_id,
        company_id=str(active.company_id), owner_id=str(active.owner_id),
        user_id=str(user["id"]), system=system, messaggio=messaggio, schema=schema,
        riserva_cents=riserva, prompt_version=versione, post=post,
    )))
    return _adesso()


async def avvia_proposta_posizioni(primary, secondary, ai, active, user: dict, call_id: Any
                                   ) -> JobPosizioniOut:
    """202: la proposta di posizioni è in preparazione (poll sul dettaglio)."""
    avviata = await _avvia_job(primary, secondary, ai, active, user, call_id,
                               partner_call_ai.SERVIZIO_POSIZIONI)
    return JobPosizioniOut(stato="in_corso", avviata_at=avviata)


async def avvia_proposta_testi(primary, secondary, ai, active, user: dict, call_id: Any
                               ) -> JobTestiOut:
    """202: la bozza dei testi è in preparazione (poll sul dettaglio)."""
    avviata = await _avvia_job(primary, secondary, ai, active, user, call_id,
                               partner_call_ai.SERVIZIO_TESTI)
    return JobTestiOut(stato="in_corso", avviata_at=avviata)


# ------------------------------------------------------------ segnalazioni


async def _profilo_segnalabile(primary, secondary, codice_pubblico: str) -> dict:
    """Snapshot pubblico di un profilo partner VISIBILE (non sospeso, azienda
    viva); altrimenti 404 come un contenuto inesistente."""
    riga = await _una(
        primary.table("company_partner_profiles").select(pps.PROFILO_SELECT)
        .eq("codice_pubblico", codice_pubblico)
        .eq("visibile_come_partner", True)
        .is_("sospeso_at", "null")
    )
    if riga is None:
        raise NotFoundError("Contenuto non trovato")
    try:
        az = await carica_azienda(primary, riga["company_profile_id"], None)
    except NotFoundError:
        raise NotFoundError("Contenuto non trovato") from None
    esercizi = await bilanci_service.carica_esercizi(primary, az.company_id)
    lookups = await _lookups(secondary)
    return profilo_pubblico(
        riga, az.dati_registro, az.dossier, calcola_fasce(esercizi) if esercizi else None,
        lookups, ident=az.ident,
    ).model_dump(mode="json")


async def _messaggio_segnalabile(primary, active, messaggio_id: str) -> dict:
    """Snapshot di un messaggio della chat (WP7) che l'azienda attiva può
    segnalare: di una conversazione di cui è parte e scritto dall'ALTRA
    azienda; altrimenti 404 come un contenuto inesistente. Mai id di utenti
    né di aziende nello snapshot."""
    if not active.company_id:
        raise NotFoundError("Contenuto non trovato")
    messaggio = await _una(
        primary.table("partner_messaggi")
        .select("id,conversazione_id,mittente_company_profile_id,testo,nascosto_moderazione_at,"
                "created_at")
        .eq("id", int(messaggio_id))
    )
    conversazione = await _una(
        primary.table("partner_conversazioni").select("id,company_creatore_id,company_partner_id")
        .eq("id", str(messaggio["conversazione_id"]))
    ) if messaggio else None
    parti = {str((conversazione or {}).get(c)) for c in ("company_creatore_id",
                                                           "company_partner_id")}
    if (
        messaggio is None or conversazione is None or str(active.company_id) not in parti
        or str(messaggio.get("mittente_company_profile_id")) == str(active.company_id)
    ):
        raise NotFoundError("Contenuto non trovato")
    nascosto = messaggio.get("nascosto_moderazione_at") is not None
    return {
        "conversazione_id": str(conversazione["id"]),
        "messaggio_id": int(messaggio["id"]),
        "testo": None if nascosto else messaggio.get("testo"),
        "nascosto": nascosto,
        "created_at": messaggio.get("created_at"),
    }


async def segnala(primary, secondary, active, user: dict, dati: SegnalazioneIn
                  ) -> SegnalazioneOut:
    """Segnalazione DSA (art. 16) di una call, di un profilo o (WP7) di un
    messaggio della chat che il segnalante può vedere (404 altrimenti), con
    lo snapshot di ciò che ha visto. Rate limit anti-abuso (fail-open, non è
    un tetto di spesa); una segnalazione aperta per contenuto e segnalante
    (409). Conferma di ricezione in-app (art. 16 c.4)."""
    settings = get_settings()
    chiave = rate_limit_service.bucket("partner_segnalazione", str(user["id"]))
    if not await rate_limit_service.allow(
        primary, chiave, settings.partner_segnalazioni_limite_giorno,
        FINESTRA_SEGNALAZIONI_SECONDI,
    ):
        raise AppError(429, "limite_segnalazioni", MSG_LIMITE_SEGNALAZIONI)
    if dati.oggetto_tipo == "call":
        call, _ = await carica_call_autorizzata(primary, dati.oggetto_id, active, user)
        oggetto_id = normalizza_id(call["id"])
        snapshot = (await _proiezione_pubblica(primary, secondary, call)).model_dump(mode="json")
    elif dati.oggetto_tipo == "messaggio":
        oggetto_id = dati.oggetto_id
        snapshot = await _messaggio_segnalabile(primary, active, oggetto_id)
    else:
        oggetto_id = dati.oggetto_id
        snapshot = await _profilo_segnalabile(primary, secondary, oggetto_id)

    identificativo = str(uuid.uuid4())
    riga = {
        "id": identificativo,
        "oggetto_tipo": dati.oggetto_tipo,
        "oggetto_id": oggetto_id,
        "segnalante_user_id": str(user["id"]),
        "segnalante_company_id": str(active.company_id) if active.company_id else None,
        "motivo": dati.motivo,
        "descrizione": dati.descrizione,
        "buona_fede": True,
        "contenuto_snapshot": snapshot,
    }
    try:
        resp = await primary.table("partner_segnalazioni").insert(riga).execute()
    except APIError as exc:
        if exc.code == "23505":
            raise AppError(409, "segnalazione_gia_presente", MSG_SEGNALAZIONE_DOPPIA) from exc
        # Mai il detail: una violazione di vincolo riporta la riga.
        logger.error("call: segnalazione non registrata (code=%s)", exc.code)
        raise UpstreamError() from exc
    salvata = resp.data[0] if resp.data and isinstance(resp.data[0], dict) else {}
    creata = _ts(salvata.get("created_at")) or _adesso()
    await notify(
        primary,
        [str(user["id"])],
        tipo=TIPO_NOTIFICA_SEGNALAZIONE,
        titolo="Abbiamo ricevuto la tua segnalazione",
        corpo=(
            "La esamineremo e ti faremo sapere la decisione. Codice della segnalazione: "
            f"{identificativo[:8]}."
        ),
        url=None,
        dedup_key=f"segnalazione:{identificativo}",
    )
    return SegnalazioneOut(id=identificativo, stato="ricevuta", created_at=creata)


# ------------------------------------------------ bacheca e scoperta (WP6)

MSG_AZIENDA_MANCANTE = "Importa o crea prima la tua azienda"
MSG_CALL_PROPRIA = "Le call della tua azienda le trovi in «Le mie call»"
# Ricontrolli live di una pagina prima di arrendersi a una pagina più corta.
_TENTATIVI_PAGINA = 3
FINESTRA_NUOVE = timedelta(days=7)


def _pesi() -> pm.PesiMatching:
    return pm.PesiMatching.da_settings(get_settings())


def _richiedi_azienda_per_te(active) -> str:
    if not active.company_id:
        raise AppError(409, "azienda_mancante", MSG_AZIENDA_MANCANTE)
    return str(active.company_id)


async def _salvate(primary, company_id: str | None) -> set[str]:
    if not company_id:
        return set()
    resp = (
        await primary.table("partner_call_salvate")
        .select("partner_call_id")
        .eq("company_profile_id", str(company_id))
        .execute()
    )
    return {str(r["partner_call_id"]) for r in resp.data or []}


async def _opt_in(primary, company_id: str | None) -> bool:
    """L'azienda ha l'opt-in visibile e il profilo non è sospeso."""
    if not company_id:
        return False
    riga = await _una(
        primary.table("company_partner_profiles").select("visibile_come_partner,sospeso_at")
        .eq("company_profile_id", str(company_id))
    )
    return bool(riga and riga.get("visibile_come_partner") is True
                and riga.get("sospeso_at") is None)


def _in_bacheca(ci, snapshot, active, oggi: date) -> bool:
    """Una call che la bacheca mostra all'azienda attiva: visibile a tutti,
    di un altro owner, attiva (non scaduta, creatore vivo)."""
    if not pubblicamente_visibile(ci.riga) or snapshot is None:
        return False
    if ci.owner_id == str(active.owner_id):
        return False
    for scadenza in (snapshot.scadenza_call, snapshot.bando_scadenza):
        if scadenza is not None and scadenza < oggi:
            return False
    return snapshot.creatore.viva


def _card(ci, idx, regioni: Mapping[int, str]) -> CallCardOut:
    vetrina = idx.vetrine.get(ci.company_id) or partenariato_indice.Vetrina()
    creatore = creatore_pubblico(vetrina.registro, vetrina.dossier, regioni)
    return call_card(ci.riga, creatore, posizioni_n=ci.posizioni_n,
                     requisiti_cercati_n=ci.requisiti_cercati_n, mia=False, ident=None)


async def _pagina_viva(elementi: list, numero: int, *, chiave, controlla, impagina):
    """Pagina `numero` di `elementi` ricontrollata live: gli elementi che non
    passano (`controlla` → insieme dei vivi) escono e la pagina si
    ricompone, al più `_TENTATIVI_PAGINA` volte (poi resta più corta). Un
    elemento morto rende vecchio l'indice: si invalida. → (pagina, restanti,
    numero di pagine)."""
    esclusi: set[str] = set()
    pagina: list = []
    restanti = elementi
    pagine: list[list] = []
    for _ in range(_TENTATIVI_PAGINA):
        restanti = [e for e in elementi if chiave(e) not in esclusi]
        pagine = impagina(restanti)
        pagina = pagine[numero - 1] if 1 <= numero <= len(pagine) else []
        vivi = await controlla([chiave(e) for e in pagina]) if pagina else set()
        morti = {chiave(e) for e in pagina} - set(vivi)
        if not morti:
            return pagina, restanti, len(pagine)
        esclusi |= morti
        partenariato_indice.invalida()
    pagina = [e for e in pagina if chiave(e) not in esclusi]
    return pagina, [e for e in restanti if chiave(e) not in esclusi], len(pagine)


def _a_fette(dimensione: int):
    def impagina(elementi: list) -> list[list]:
        return [elementi[i : i + dimensione] for i in range(0, len(elementi), dimensione)]
    return impagina


async def bacheca(
    primary, secondary, active, user: dict, *, vista: str = "tutte", bando: str | None = None,
    regione: int | None = None, forma: str | None = None, ruolo: str | None = None,
    ordine: str = "affinita", page: int = 1, page_size: int = 20,
) -> Page[CallBachecaOut]:
    """GET /partenariati/call: `mie` (le call dell'azienda attiva), `tutte`
    (call pubblicate e visibili a tutti di ALTRI owner) o `salvate` (quelle
    seguite, ancora visibili). Filtri: bando (slug o id), regione (sede del
    creatore o regioni richieste), forma prevista, ruolo offerto (capofila o
    partner); ordine per affinità (il proprio match prima, per requisiti
    coperti e punteggio), recenti o scadenza. Ogni pagina è ricontrollata
    live (call ancora pubblicate, creatore vivo)."""
    if vista == "mie":
        return await lista_mie(primary, secondary, active, user, page=page, page_size=page_size)
    oggi = _oggi()
    idx = await partenariato_indice.indice(primary, secondary)
    salvate = await _salvate(primary, active.company_id)
    elenco = []
    for ci in idx.bacheca.values():
        snapshot = idx.matching.calls.get(ci.id)
        if not _in_bacheca(ci, snapshot, active, oggi):
            continue
        if vista == "salvate" and ci.id not in salvate:
            continue
        if bando and bando not in (str(ci.riga.get("bando_id")), ci.riga.get("bando_slug")):
            continue
        if regione is not None and regione not in ci.regioni_ids:
            continue
        if forma and ci.riga.get("forma_aggregazione_prevista") != forma:
            continue
        if ruolo and ruolo not in ci.ruoli:
            continue
        elenco.append((ci, snapshot))
    profilo = (
        await partenariato_indice.profilo_azienda(primary, idx, active.company_id)
        if active.company_id else None
    )
    pesi = _pesi()
    match = {
        ci.id: pm.valuta_coppia(snapshot, profilo, oggi=oggi, pesi=pesi, direzione="per_te")
        for ci, snapshot in elenco
    } if profilo is not None else {}

    def recente(ci) -> str:
        return str(ci.riga.get("pubblicata_at") or "")

    if ordine == "scadenza":
        elenco.sort(key=lambda e: (e[1].scadenza_call is None, e[1].scadenza_call or oggi,
                                   e[0].id))
    else:
        elenco.sort(key=lambda e: recente(e[0]), reverse=True)
        if ordine == "affinita":
            elenco.sort(key=lambda e: (
                match.get(e[0].id) is None,
                -(match[e[0].id].coperti if match.get(e[0].id) else 0),
                -(match[e[0].id].punteggio if match.get(e[0].id) else 0),
            ))
    pagina, restanti, _ = await _pagina_viva(
        elenco, page,
        chiave=lambda e: e[0].id,
        controlla=lambda ids: partenariato_indice.ricontrollo_call_live(primary, ids, oggi=oggi),
        impagina=_a_fette(page_size),
    )
    regioni = nomi_regioni(await _lookups(secondary))
    ricevute = await candidature_ricevute(primary, [ci.id for ci, _snapshot in pagina])
    items = [
        call_bacheca(
            _card(ci, idx, regioni),
            match=pm.proietta_match(match[ci.id], vista="proprio") if match.get(ci.id) else None,
            salvata=ci.id in salvate,
            posti=ci.posti,
            candidature_ricevute=ricevute.get(ci.id, 0),
        )
        for ci, _snapshot in pagina
    ]
    return Page.build(items, len(restanti), page, page_size)


async def per_te(primary, secondary, active, user: dict, *, page: int = 1,
                 page_size: int = 20) -> PerTeOut:
    """«Per te»: le call che l'azienda attiva completerebbe (almeno un
    requisito cercato o una posizione coperti), ordinate come i suggeriti e
    con al massimo 2 call dello stesso owner per pagina. Anche senza opt-in
    (solo scoperta, Q25: `opt_in` false e CTA); per candidarsi serve. Le call
    solo su invito compaiono solo all'azienda invitata (WP7)."""
    company_id = _richiedi_azienda_per_te(active)
    oggi = _oggi()
    idx = await partenariato_indice.indice(primary, secondary)
    profilo = await partenariato_indice.profilo_azienda(primary, idx, company_id)
    opt_in = await _opt_in(primary, company_id)
    if profilo is None:
        return PerTeOut(items=[], total=0, page=page, page_size=page_size, total_pages=0,
                        opt_in=opt_in)
    pesi = _pesi()
    risultati = pm.per_te(idx.matching, profilo, oggi=oggi, pesi=pesi)
    # WP7: le call solo su invito compaiono all'azienda invitata (invito in
    # attesa o accettato): il ricontrollo live le ammette solo per lei.
    su_invito = {cid for cid, aziende in idx.matching.inviti.items() if company_id in aziende}

    async def controlla(ids: list[str]) -> set[str]:
        vive = await partenariato_indice.ricontrollo_call_live(
            primary, [i for i in ids if i not in su_invito], oggi=oggi)
        invitate = [i for i in ids if i in su_invito]
        if invitate:
            vive |= await partenariato_indice.ricontrollo_call_live(
                primary, invitate, oggi=oggi, solo_pubbliche=False)
        return vive

    pagina, restanti, n_pagine = await _pagina_viva(
        risultati, page,
        chiave=lambda m: m.call_id,
        controlla=controlla,
        impagina=lambda elementi: pm.impagina(
            elementi, dimensione=page_size, max_per_owner=pesi.max_per_owner_pagina,
            owner=pm.owner_call,
        ),
    )
    salvate = await _salvate(primary, company_id)
    regioni = nomi_regioni(await _lookups(secondary))
    ricevute = await candidature_ricevute(primary, [m.call_id for m in pagina])
    items = [
        call_bacheca(
            _card(idx.bacheca[m.call_id], idx, regioni),
            match=pm.proietta_match(m, vista="proprio"),
            salvata=m.call_id in salvate,
            posti=idx.bacheca[m.call_id].posti,
            candidature_ricevute=ricevute.get(m.call_id, 0),
        )
        for m in pagina
        if m.call_id in idx.bacheca
    ]
    return PerTeOut(items=items, total=len(restanti), page=page, page_size=page_size,
                    total_pages=n_pagine, opt_in=opt_in)


async def suggeriti(primary, secondary, active, user: dict, call_id: Any, *, page: int = 1,
                    posizione_id: str | None = None) -> SuggeritiOut:
    """Aziende suggerite al creatore della call (titolare e membri in
    lettura): solo call pubblicate; pseudonimo per call, mai
    `company_profile_id`; match in vista «terzi»; al massimo 2 aziende dello
    stesso owner sull'intera lista (`pm.suggeriti_per_call`); ricontrollo
    live di opt-in, sospensione, azienda viva (e `accetta_inviti` per le call
    solo su invito) su ogni pagina, anche con l'indice fresco (revoca
    immediata)."""
    call, _ = await carica_call_autorizzata(primary, call_id, active, user, ammessi=RUOLI_AZIENDA)
    dimensione = max(1, get_settings().partenariato_suggeriti_pagina)
    vuota = SuggeritiOut(items=[], total=0, page=page, page_size=dimensione, total_pages=0)
    if call.get("stato") != "pubblicata":
        return vuota
    idx = await partenariato_indice.indice(primary, secondary)
    cid = str(call["id"])
    if cid not in idx.matching.calls:
        return vuota
    pesi = _pesi()
    risultati = pm.suggeriti_per_call(
        idx.matching, cid, pm.FiltriSuggeriti(posizione_id=posizione_id),
        oggi=_oggi(), pesi=pesi,
    )
    solo_invitati = call.get("visibilita") == "solo_invitati"

    async def controlla(ids: list[str]) -> set[str]:
        vivi = await partenariato_indice.ricontrollo_live(primary, ids)
        return {c for c, dati in vivi.items() if not solo_invitati or dati["accetta_inviti"]}

    pagina, restanti, n_pagine = await _pagina_viva(
        risultati, page,
        chiave=lambda m: m.company_id,
        controlla=controlla,
        impagina=lambda elementi: pm.impagina(
            elementi, dimensione=dimensione, max_per_owner=pesi.max_per_owner_pagina,
        ),
    )
    profili = await _profili_pubblici(
        primary, secondary, [m.company_id for m in pagina], idx.matching.candidati
    )
    contatti = await _stati_contatto(primary, cid, [m.company_id for m in pagina])
    items = [
        candidato_suggerito(m, call_id=cid, profilo=profili[m.company_id],
                            stato_contatto=contatti.get(m.company_id))
        for m in pagina
        if m.company_id in profili
    ]
    return SuggeritiOut(items=items, total=len(restanti), page=page, page_size=dimensione,
                        total_pages=n_pagine)


# Quale stato del contatto mostrare se con la stessa azienda ce n'è più di uno.
_PRIORITA_CONTATTO = {"accettata": 0, "inviata": 1, "rifiutata": 2}


async def _stati_contatto(primary, call_id: str, ids: list[str]) -> dict[str, str]:
    """Stato del contatto della call con le aziende di una pagina di
    suggeriti (WP7), con una lettura: `accettata`, poi `inviata` (in attesa;
    un invito oltre la scadenza non conta), poi `rifiutata` solo per un
    INVITO rifiutato (non si reinvita). Nulla per le righe ritirate o
    scadute: si può invitare di nuovo."""
    if not ids:
        return {}
    resp = (
        await primary.table("partner_candidature")
        .select("company_profile_id,tipo,stato,scade_at")
        .eq("partner_call_id", call_id)
        .in_("company_profile_id", ids)
        .execute()
    )
    stati: dict[str, str] = {}
    for riga in resp.data or []:
        stato = stato_effettivo(riga)
        if stato not in _PRIORITA_CONTATTO or (stato == "rifiutata"
                                               and riga.get("tipo") != "invito"):
            continue
        azienda = str(riga.get("company_profile_id"))
        if azienda not in stati or _PRIORITA_CONTATTO[stato] < _PRIORITA_CONTATTO[stati[azienda]]:
            stati[azienda] = stato
    return stati


PROFILO_PUBBLICO_SELECT = (
    "company_profile_id,codice_pubblico,anonimo,accetta_inviti,descrizione_competenze,"
    "competenze,competenze_libere,tipi_soggetto,ruoli_disponibili,regioni_interesse,"
    "paesi_interesse,forme_accettate,esperienze,certificazioni,infrastrutture"
)


async def _profili_pubblici(primary, secondary, ids: list[str], candidati: Mapping) -> dict:
    """Profili pubblici (WP4, whitelist) delle aziende di una pagina di
    suggeriti, letti a blocco (profilo, azienda, registro, persone: quattro
    letture per pagina). Le fasce vengono dal profilo di matching (stessi
    bilanci); gli identificativi dell'azienda tolgono i riferimenti dai testi
    liberi degli anonimi. Un'azienda sparita nel frattempo manca."""
    if not ids:
        return {}
    profili, aziende, dati, persone, lookups = await asyncio.gather(
        primary.table("company_partner_profiles").select(PROFILO_PUBBLICO_SELECT)
        .in_("company_profile_id", ids).execute(),
        primary.table("company_profiles").select(AZIENDA_SELECT).in_("id", ids).execute(),
        primary.table("company_data").select(f"company_profile_id,{COMPANY_DATA_SELECT}")
        .in_("company_profile_id", ids).execute(),
        primary.table("company_people").select(f"company_profile_id,{PERSONE_SELECT}")
        .in_("company_profile_id", ids).execute(),
        _lookups(secondary),
    )
    per_id = {str(r["company_profile_id"]): r for r in profili.data or []}
    registri = {str(r["company_profile_id"]): r for r in dati.data or []}
    nomi: dict[str, list[dict]] = {}
    for riga in persone.data or []:
        nomi.setdefault(str(riga["company_profile_id"]), []).append(riga)
    uscita: dict = {}
    for company in aziende.data or []:
        cid = str(company["id"])
        riga = per_id.get(cid)
        if riga is None or cid not in candidati:
            continue
        az = Azienda(company_id=cid, company=company, company_data=registri.get(cid),
                     persone=nomi.get(cid, []), profilo_partner=riga)
        uscita[cid] = profilo_pubblico(riga, az.dati_registro, az.dossier,
                                       candidati[cid].fasce, lookups, ident=az.ident)
    return uscita


async def _match_proprio(primary, secondary, active, call: Mapping):
    """Il match dell'azienda attiva con una call di altri (vista «proprio»,
    con i propri valori nelle regole finanziarie). None se l'azienda manca,
    se la coppia è esclusa o se l'indice non è disponibile (best-effort: la
    vista pubblica non dipende dal matching)."""
    if not active.company_id:
        return None
    try:
        idx = await partenariato_indice.indice(primary, secondary)
        snapshot = idx.matching.calls.get(str(call["id"]))
        if snapshot is None:
            return None
        profilo = await partenariato_indice.profilo_azienda(primary, idx, active.company_id)
        if profilo is None:
            return None
        # WP7: una call solo su invito è compatibile per chi è stato invitato.
        m = pm.valuta_coppia(snapshot, profilo, oggi=_oggi(), pesi=_pesi(),
                             direzione="per_te", dettaglio_proprio=True,
                             invitati=idx.matching.inviti.get(str(call["id"]), ()))
    except Exception as exc:  # noqa: BLE001 — il match è un di più
        logger.warning("call: match non calcolato (call %s, %s)", call.get("id"),
                       getattr(exc, "code", None) or type(exc).__name__)
        return None
    return pm.proietta_match(m, vista="proprio") if m is not None else None


def _visibile_ora(call: Mapping) -> bool:
    scadenza = _data(call.get("scadenza_call"))
    return pubblicamente_visibile(call) and not (scadenza is not None and scadenza < _oggi())


async def _dettaglio_pubblico(
    primary, secondary, active, call: dict, *, ruolo: str = "pubblico"
) -> CallPubblicaDettaglioOut:
    """La call di un'altra azienda: proiezione pubblica (whitelist del WP5),
    il proprio match, se è salvata e se l'azienda attiva ha l'opt-in. Solo
    call visibili adesso (una call scaduta ma non ancora chiusa dallo
    scheduler è 404, senza scritture); l'admin qui è un visitatore come gli
    altri. WP7: chi ha una candidatura o un invito IN ATTESA (`ruolo`) la
    vede anche se è solo su invito o scaduta; una candidatura chiusa
    (rifiutata, ritirata, scaduta) si vede con la call solo finché la call
    è visibile a tutti."""
    if ruolo in ("candidato", "invitato"):
        riga = await candidatura_su_call(primary, call["id"], active.company_id)
        if riga is None:
            raise NotFoundError("Call di partenariato non trovata")
    elif not _visibile_ora(call):
        raise NotFoundError("Call di partenariato non trovata")
    else:
        riga = await candidatura_su_call(primary, call["id"], active.company_id)
    candidatura = candidatura_propria(riga, editable=bool(active.editable)) if riga else None
    az = await carica_azienda(primary, call["company_profile_id"], call["family_parent_id"],
                              viva=False)
    requisiti, posizioni = await asyncio.gather(
        _requisiti(primary, call["id"]), _posizioni(primary, call["id"])
    )
    pubblica = await _proiezione_pubblica(primary, secondary, call, az, requisiti=requisiti,
                                          posizioni=posizioni)
    salvate = await _salvate(primary, active.company_id)
    return CallPubblicaDettaglioOut(
        **pubblica.model_dump(),
        match=await _match_proprio(primary, secondary, active, call),
        salvata=str(call["id"]) in salvate,
        opt_in=await _opt_in(primary, active.company_id),
        candidatura=candidatura,
        requisiti_dichiarabili=requisiti_dichiarabili(requisiti, az.ident),
    )


async def _vista_controparte(primary, secondary, active, call: dict) -> CallVistaControparteOut:
    """La call per la controparte accettata (WP7): proiezione pubblica più
    dettagli riservati e budget esatto; l'identità del creatore solo con la
    rivelazione accesa e il suo audit (`identita_se_rivelata`)."""
    # Import locale: il servizio delle candidature importa questo modulo.
    from app.services import partenariato_candidature_service as candidature

    riga = await candidatura_su_call(primary, call["id"], active.company_id)
    if riga is None or riga.get("stato") != "accettata":
        raise NotFoundError("Call di partenariato non trovata")
    az = await carica_azienda(primary, call["company_profile_id"], call["family_parent_id"],
                              viva=False)
    pubblica = await _proiezione_pubblica(primary, secondary, call, az)
    identita = await candidature.identita_se_rivelata(primary, riga["id"], az.company_id)
    return vista_controparte(
        pubblica, call, candidatura_propria(riga, editable=bool(active.editable)),
        ident=az.ident, identita=identita,
    )


async def match_call(primary, secondary, active, user: dict, call_id: Any):
    """GET /partenariati/call/{id}/match: il match dell'azienda attiva con
    la call (vista «proprio»); null per le call della propria azienda o se
    la coppia è esclusa."""
    call, ruolo = await carica_call_autorizzata(primary, call_id, active, user)
    if ruolo in RUOLI_AZIENDA:
        return None
    if not _visibile_ora(call):
        raise NotFoundError("Call di partenariato non trovata")
    return await _match_proprio(primary, secondary, active, call)


async def salva(primary, secondary, active, user: dict, call_id: Any) -> None:
    """Salva («segui») una call visibile di un altro owner (titolare, T4):
    idempotente. La propria (o di un'altra azienda dello stesso owner) →
    409 `call_propria`; una call non visibile → 404."""
    _richiedi_titolare(active)
    company_id = _richiedi_azienda(active)
    call, ruolo = await carica_call_autorizzata(primary, call_id, active, user)
    if ruolo in RUOLI_AZIENDA or str(call.get("family_parent_id")) == str(active.owner_id):
        raise AppError(409, "call_propria", MSG_CALL_PROPRIA)
    if not _visibile_ora(call):
        raise NotFoundError("Call di partenariato non trovata")
    await primary.table("partner_call_salvate").upsert(
        {"company_profile_id": company_id, "partner_call_id": str(call["id"]),
         "user_id": str(user["id"])},
        on_conflict="company_profile_id,partner_call_id",
        ignore_duplicates=True,
    ).execute()


async def rimuovi_salvata(primary, secondary, active, user: dict, call_id: Any) -> None:
    """Smette di seguire la call (titolare): idempotente, anche se la call
    non è più visibile."""
    _richiedi_titolare(active)
    company_id = _richiedi_azienda(active)
    identificativo = normalizza_id(call_id)
    await primary.table("partner_call_salvate").delete().eq(
        "company_profile_id", company_id
    ).eq("partner_call_id", identificativo).execute()


async def riepilogo_partenariati(primary, secondary, active, user: dict) -> RiepilogoOut:
    """Badge del menu «Partenariati»: call «Per te» pubblicate negli ultimi 7
    giorni, call pubblicate dell'azienda attiva, call salvate ancora
    visibili."""
    if not active.company_id:
        return RiepilogoOut()
    oggi = _oggi()
    idx = await partenariato_indice.indice(primary, secondary)
    profilo = await partenariato_indice.profilo_azienda(primary, idx, active.company_id)
    soglia = (_adesso() - FINESTRA_NUOVE).isoformat()
    nuove = 0
    if profilo is not None:
        for m in pm.per_te(idx.matching, profilo, oggi=oggi, pesi=_pesi()):
            ci = idx.bacheca.get(m.call_id)
            if ci is not None and _ts(ci.riga.get("pubblicata_at")) and (
                _ts(ci.riga.get("pubblicata_at")) >= _ts(soglia)
            ):
                nuove += 1
    attive = (
        await primary.table("partner_calls")
        .select("id", count="exact")
        .eq("company_profile_id", str(active.company_id))
        .eq("family_parent_id", str(active.owner_id))
        .eq("stato", "pubblicata")
        .limit(1)
        .execute()
    )
    salvate = await _salvate(primary, active.company_id)
    visibili = sum(
        1 for cid in salvate
        if cid in idx.bacheca
        and _in_bacheca(idx.bacheca[cid], idx.matching.calls.get(cid), active, oggi)
    )
    # WP7 (import locale: quei servizi importano questo modulo).
    from app.services import partenariato_candidature_service, partenariato_chat_service

    candidature = await partenariato_candidature_service.conteggi_riepilogo(primary, active)
    non_letti = await partenariato_chat_service.non_letti_totali(primary, active, user)
    return RiepilogoOut(per_te_nuove=nuove, call_attive=attive.count or 0, salvate=visibili,
                        messaggi_non_letti=non_letti, **candidature)
