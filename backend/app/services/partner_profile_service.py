"""Profilo partner dell'azienda (WP4, docs/partenariati.md §2.4 P1-P7).

Chi agisce (T4): scrive solo il titolare (`active.editable`), i membri con
visibilità sull'azienda leggono; per l'Advisor vale l'azienda attiva. Unica
eccezione: la risposta del membro proposto come referente (Q13), che è sua.

Scritture:
- `salva_profilo`: upsert a WHITELIST dei soli campi liberi (+ chiavi e
  completezza); visibilità, anonimato, consenso, referente e sospensione non ci
  sono MAI (il trigger con GUC della 0035 li rifiuterebbe comunque). Ogni testo
  libero si salva senza caratteri invisibili e passa da `trova_rilievi` (sulla
  forma canonica, come le call): contatti sempre bloccanti, identificativi
  dell'azienda bloccanti se il profilo è anonimo, cognomi delle persone del
  registro solo come avvisi;
- `consenso` → `fn_partner_consenso` (registro append-only + audit nella
  stessa transazione), con l'identità dal registro (T5) e, in produzione, dati
  non sandbox. Il profilo NOMINATIVO è spento (`NOMINATIVO_DISPONIBILE`): si
  compare solo in forma anonima;
- `referente` / `risposta_referente` → `fn_partner_referente`;
- `avvia_bozza_ai` → prenotazione fail-closed (`fn_partner_bozza_ai_prenota`:
  limite per azienda, limite per titolare e budget del gruppo `altri`) e job
  in-process (`_esegui_bozza`, non solleva mai) → 202; la proposta resta in
  `bozza_ai` finché l'utente non la applica e salva. Il job chiude bozza ed
  esecuzione in UNA transazione (`fn_partner_bozza_ai_concludi`) e registra il
  consumo solo se ha chiuso lui l'esecuzione: altrimenti l'ha chiusa (e
  registrata) il failsafe.

Letture: `get_profilo` (con il failsafe delle bozze orfane) e `anteprima`,
cioè la proiezione verso terzi (`partner_profilo_pubblico.profilo_pubblico`)
del proprio profilo anche se non visibile. Il referente conta solo se la sua
membership è ANCORA attiva con accesso all'azienda (verifica in lettura).

Log: mai P.IVA, CF, nomi o testi del profilo; solo id dell'azienda e codici.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from postgrest.exceptions import APIError
from pydantic import ValidationError

from app.core.config import get_settings
from app.core.errors import (
    AiNotConfiguredError,
    AiTimeoutError,
    AiUpstreamError,
    AppError,
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    UpstreamError,
)
from app.schemas.partner_profile import (
    BozzaAiOut,
    BozzaProfiloAi,
    BozzaProfiloAiOut,
    ConsensoIn,
    ConsensoStatoOut,
    EsperienzaPartnerOut,
    IdentitaPartnerOut,
    PartnerProfileIn,
    PartnerProfileOut,
    PartnerPubblicoOut,
    ProfiloPartnerDati,
    ReferenteIn,
    ReferenteOut,
    ReferentePossibileOut,
    ReferentePropostoOut,
    ReferenteRispostaIn,
)
from app.services import bilanci_service, lookup_service, partenariato_indice
from app.services import partenariato_vocabolario as voc
from app.services.ai_prezzi import costo_cents, stima_cents
from app.services.bilanci_indicatori import calcola_fasce
from app.services.openapi_mapping import build_dossier
from app.services.openapi_service import record_usage
from app.services.partenariati_ai_budget import budget_cents_gruppo
from app.services.partenariato_anonimato import (
    ETICHETTE_RILIEVO,
    Identificativi,
    identificativi_azienda,
    senza_invisibili,
    trova_rilievi,
)
from app.services.partenariato_errori import raise_from_rpc
from app.services.partenariato_informativa import (
    INFORMATIVA_PARTNER_VERSIONE,
    INFORMATIVA_REFERENTE_VERSIONE,
)
from app.services.partner_profile_prompts import (
    PROFILO_PROMPT_VERSION,
    SYSTEM_PROFILO,
    build_profilo_input,
    pulisci_bozza,
    schema_json,
)
from app.services.partner_profilo_pubblico import (
    completezza,
    profilo_pubblico,
    tipi_soggetto_dedotti,
)

logger = logging.getLogger("bandofit.partenariati")

SERVIZIO = "partner_profilo_ai"
MSG_SOLO_TITOLARE = "Il profilo partner lo gestisce il titolare dell'azienda"
MSG_AI_NON_CONFIGURATA = "Generazione automatica non configurata su questo ambiente"
MSG_SENZA_AZIENDA = "Nessuna azienda attiva: crea prima l'azienda"
MSG_NOMINATIVO_NON_DISPONIBILE = (
    "Per ora le aziende compaiono solo in forma anonima: mostrare il nome sarà possibile "
    "con una verifica della rappresentanza dell'impresa"
)
MSG_LIMITE_BOZZE_UTENTE = "Hai raggiunto le bozze di oggi: riprova domani"
# Profilo NOMINATIVO (Q9): spento finché non esiste una prova forte che chi
# agisce rappresenti l'impresa. La verifica del codice fiscale del profilo da
# sola non basta a dimostrarlo, quindi oggi si compare solo in forma anonima.
# Si riaccende SOLO insieme a quella verifica (non è una setting d'ambiente).
NOMINATIVO_DISPONIBILE = False
# Rivelazione dell'IDENTITÀ all'accettazione di una candidatura o di un invito
# (WP7, K2, Q13): implementata ma SPENTA per la stessa ragione del profilo
# nominativo. Spenta, le due aziende restano anonime l'una per l'altra anche
# dopo l'accettazione (proiezioni anonime e pseudonimo, chat con il banner
# sull'identità non verificata) e `fn_partner_decidi` non scrive l'audit di
# rivelazione. Accesa: ragione sociale, sito e PEC dal registro, nome e ruolo
# del referente (mai la sua email), con l'audit nella RPC. Costante, non
# setting: si riaccende solo insieme alla verifica della rappresentanza.
RIVELAZIONE_IDENTITA_DISPONIBILE = False
# Tempo massimo per chiudere la bozza quando il task viene cancellato
# (spegnimento del processo): poi ci pensa il failsafe.
CHIUSURA_SU_CANCELLAZIONE_SECONDI = 5.0
# Handle dell'anteprima di un profilo mai salvato (nessuna riga, nessun codice).
CODICE_PUBBLICO_ASSENTE = UUID(int=0)

# Campi che l'utente modifica (PUT): la WHITELIST dell'upsert.
CAMPI_LIBERI: tuple[str, ...] = tuple(PartnerProfileIn.model_fields)
# Campi che cambiano SOLO tramite RPC (trigger con GUC della 0035): mai
# nell'upsert, nemmeno al valore di default.
CAMPI_PROTETTI: frozenset[str] = frozenset(
    {
        "visibile_come_partner",
        "anonimo",
        "consenso_versione",
        "consenso_at",
        "referente_user_id",
        "referente_proposto_user_id",
        "referente_proposto_at",
        "sospeso_at",
        "sospeso_motivo",
        "sospeso_da",
        "codice_pubblico",
    }
)

PROFILO_SELECT = (
    "company_profile_id,family_parent_id,codice_pubblico,visibile_come_partner,anonimo,"
    "consenso_versione,consenso_at,accetta_inviti,descrizione_competenze,competenze,"
    "competenze_libere,tipi_soggetto,ruoli_disponibili,settori_interesse,regioni_interesse,"
    "paesi_interesse,forme_accettate,esperienze,certificazioni,infrastrutture,"
    "categorie_bando_escluse,vocabolario_versione,completezza,referente_user_id,"
    "referente_proposto_user_id,referente_proposto_at,sospeso_at,bozza_ai,bozza_ai_stato,"
    "bozza_ai_avviata_at,bozza_ai_at,bozza_ai_errore,bozza_ai_esecuzione_id,updated_at"
)
_AZIENDA_SELECT = "id,parent_id,ragione_sociale,partita_iva,codice_fiscale,sito_web"
_COMPANY_DATA_SELECT = "raw,derived,piva_fetched,sandbox,denominazione,stato_impresa"
_PERSONE_SELECT = "nome,cognome,codice_fiscale,is_legale_rappresentante"

_MESSAGGI_BOZZA = {
    "timeout": "La bozza ha richiesto troppo tempo: riprova più tardi",
    "interrotta": "La preparazione della bozza si è interrotta: riprova",
    "ai_risposta_non_valida": "La bozza non è venuta bene: riprova più tardi",
    "ai_non_disponibile": "Il servizio che prepara la bozza non è disponibile: riprova più tardi",
}
_MESSAGGIO_BOZZA = "Non siamo riusciti a preparare la bozza: riprova più tardi"

# Testi liberi del profilo, con il nome del campo per i messaggi.
_ETICHETTE_CAMPI = {
    "descrizione_competenze": "La descrizione delle competenze",
    "competenze_libere": "Le competenze aggiuntive",
    "programma": "Il programma di un'esperienza",
    "titolo": "Il titolo di un'esperienza",
    "certificazioni": "Le certificazioni",
    "infrastrutture": "La descrizione delle infrastrutture",
}

# Riferimenti ai job in corso: senza, il garbage collector può cancellare un
# task fire-and-forget a metà esecuzione.
_background_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    """Avvia il job della bozza in background (sostituibile nei test)."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


# ------------------------------------------------------------------ utilità


def _adesso() -> datetime:
    return datetime.now(timezone.utc)


def _ts(valore) -> datetime | None:
    if isinstance(valore, datetime):
        return valore if valore.tzinfo else valore.replace(tzinfo=timezone.utc)
    if not valore:
        return None
    try:
        parsed = datetime.fromisoformat(str(valore).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _cf(valore) -> str:
    """Stesso confronto della RPC: `upper(btrim(...))`."""
    return valore.strip().upper() if isinstance(valore, str) else ""


def richiedi_non_sandbox() -> bool:
    """Dati del registro di sandbox NON ammessi per il consenso. Fail-closed:
    li ammette solo un ambiente openapi dichiarato `sandbox` (contratto: vale
    per `production`; qualunque valore non riconosciuto conta come tale)."""
    return get_settings().openapi_env.strip().lower() != "sandbox"


def _richiedi_azienda(active) -> str:
    if not active.company_id:
        raise NotFoundError(MSG_SENZA_AZIENDA)
    return str(active.company_id)


def _richiedi_titolare(active) -> None:
    if not active.editable:
        raise ForbiddenError(MSG_SOLO_TITOLARE)


def _informativa_superata(referente: bool = False) -> AppError:
    cosa = "per il referente " if referente else ""
    return AppError(
        409,
        "informativa_superata",
        f"L'informativa {cosa}è stata aggiornata: rileggila e conferma di nuovo",
    )


# ------------------------------------------------------------ letture DB


@dataclass
class _Contesto:
    company_id: str
    owner_id: str
    azienda: dict
    company_data: dict | None
    persone: list[dict]
    profilo: dict | None

    @property
    def dossier(self) -> dict:
        raw = (self.company_data or {}).get("raw")
        return build_dossier(raw) if isinstance(raw, dict) else {}

    @property
    def ident(self) -> Identificativi:
        return identificativi_azienda(self.azienda, self.company_data, self.persone)

    @property
    def anonimo(self) -> bool:
        return (self.profilo or {}).get("anonimo") is not False


async def _azienda(primary, company_id: str, owner_id: str) -> dict:
    resp = (
        await primary.table("company_profiles")
        .select(_AZIENDA_SELECT)
        .eq("id", company_id)
        .eq("parent_id", owner_id)
        .is_("deleted_at", "null")
        .is_("archived_at", "null")
        .limit(1)
        .execute()
    )
    if not resp.data:
        raise NotFoundError("Azienda non trovata")
    return resp.data[0]


async def _company_data(primary, company_id: str) -> dict | None:
    resp = (
        await primary.table("company_data")
        .select(_COMPANY_DATA_SELECT)
        .eq("company_profile_id", company_id)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


async def _persone(primary, company_id: str) -> list[dict]:
    resp = (
        await primary.table("company_people")
        .select(_PERSONE_SELECT)
        .eq("company_profile_id", company_id)
        .execute()
    )
    return [p for p in (resp.data or []) if isinstance(p, dict)]


async def _profilo(primary, company_id: str) -> dict | None:
    resp = (
        await primary.table("company_partner_profiles")
        .select(PROFILO_SELECT)
        .eq("company_profile_id", company_id)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


async def _contesto(primary, active) -> _Contesto:
    company_id = _richiedi_azienda(active)
    owner_id = str(active.owner_id)
    azienda, dati, persone, profilo = await asyncio.gather(
        _azienda(primary, company_id, owner_id),
        _company_data(primary, company_id),
        _persone(primary, company_id),
        _profilo(primary, company_id),
    )
    return _Contesto(company_id, owner_id, azienda, dati, persone, profilo)


async def _titolare(primary, user: dict, owner_id: str) -> dict:
    """Profilo del titolare (nome, CF e verifica del CF): è l'utente stesso,
    oppure si legge per un membro."""
    if str(user.get("id")) == owner_id:
        return user
    resp = (
        await primary.table("profiles")
        .select("id,nome,cognome,codice_fiscale,cf_verified_at")
        .eq("id", owner_id)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else {}


async def _membri_con_accesso(primary, owner_id: str, company_id: str) -> dict[str, str]:
    """Membri ATTIVI del titolare con visibilità sull'azienda: user_id → nome
    (quello scelto dal titolare all'invito). Vale in lettura per il referente
    effettivo: una membership finita o senza accesso non conta più."""
    accessi = (
        await primary.table("family_member_company_access")
        .select("family_member_id")
        .eq("company_profile_id", company_id)
        .execute()
    )
    ids = [str(r["family_member_id"]) for r in accessi.data or [] if r.get("family_member_id")]
    if not ids:
        return {}
    resp = (
        await primary.table("family_members")
        .select("id,member_id,denominazione")
        .eq("parent_id", owner_id)
        .eq("status", "active")
        .in_("id", ids)
        .execute()
    )
    return {
        str(r["member_id"]): (r.get("denominazione") or "").strip() or "Membro dell'azienda"
        for r in resp.data or []
        if r.get("member_id")
    }


async def _lookups_opzionali(secondary):
    try:
        return await lookup_service.get_lookups(secondary)
    except Exception as exc:  # noqa: BLE001 — le lookup servono solo ai nomi
        logger.warning("partner: lookup del catalogo non disponibili (%s)", type(exc).__name__)
        return None


async def _failsafe_bozza(primary, ctx: _Contesto) -> None:
    """Bozza `in_corso` da più di `partner_bozza_ai_stale_minuti` (processo
    riavviato, job perso): la chiude la RPC (errore `interrotta`, costo
    ignoto) e si rilegge. Best-effort: la lettura non fallisce per questo."""
    riga = ctx.profilo or {}
    if riga.get("bozza_ai_stato") != "in_corso":
        return
    minuti = get_settings().partner_bozza_ai_stale_minuti
    avviata = _ts(riga.get("bozza_ai_avviata_at"))
    if avviata is not None and _adesso() - avviata < timedelta(minutes=minuti):
        return
    try:
        await primary.rpc("fn_partner_bozza_ai_chiudi_stale", {"p_minuti": minuti}).execute()
        ctx.profilo = await _profilo(primary, ctx.company_id)
    except Exception:
        logger.exception("partner: failsafe delle bozze AI non riuscito")


# ---------------------------------------------------------- composizione


def _dedotti(ctx: _Contesto) -> list[str]:
    """Tipi di soggetto dal SOLO registro (T5)."""
    dati = ctx.company_data or {}
    dossier = ctx.dossier
    anagrafica = dossier.get("anagrafica") or {}
    forma = " ".join(
        p for p in (anagrafica.get("forma_giuridica"), anagrafica.get("forma_giuridica_dettaglio"))
        if isinstance(p, str) and p.strip()
    )
    return tipi_soggetto_dedotti(dati.get("derived") or {}, dossier.get("flags"), forma or None)


def _identita(ctx: _Contesto, titolare: dict) -> IdentitaPartnerOut:
    """Stessi controlli di `fn_partner_consenso` (T5 e Q9), per la UI."""
    dati = ctx.company_data
    piva = ctx.azienda.get("partita_iva")
    motivo = None
    if dati is None:
        motivo = "dati_non_importati"
    elif not piva or dati.get("piva_fetched") != piva:
        motivo = "piva_diversa"
    elif str(dati.get("stato_impresa") or "").strip().lower() != "attiva":
        motivo = "impresa_non_attiva"
    elif dati.get("sandbox") is not False and richiedi_non_sandbox():
        motivo = "dati_sandbox"
    denominazione = None
    if dati is not None and piva and dati.get("piva_fetched") == piva:
        denominazione = (dati.get("denominazione") or "").strip() or None

    cf = _cf(titolare.get("codice_fiscale"))
    if not NOMINATIVO_DISPONIBILE:
        motivo_nominativo = "non_disponibile"
    elif not cf or not titolare.get("cf_verified_at"):
        motivo_nominativo = "cf_non_verificato"
    elif not any(
        p.get("is_legale_rappresentante") is True and _cf(p.get("codice_fiscale")) == cf
        for p in ctx.persone
    ):
        motivo_nominativo = "non_rappresentante"
    else:
        motivo_nominativo = None
    return IdentitaPartnerOut(
        verificata=motivo is None,
        motivo=motivo,
        denominazione_registro=denominazione,
        puo_essere_nominativo=motivo is None and motivo_nominativo is None,
        motivo_nominativo=motivo_nominativo,
    )


def _nome(persona: dict) -> str | None:
    nome = " ".join(
        p.strip() for p in (persona.get("nome"), persona.get("cognome"))
        if isinstance(p, str) and p.strip()
    )
    return nome or None


def _referente(
    riga: dict, user: dict, owner_id: str, titolare: dict, membri: dict[str, str]
) -> ReferenteOut:
    user_id = str(user.get("id"))
    effettivo = str(riga.get("referente_user_id") or "")
    proposto_id = str(riga.get("referente_proposto_user_id") or "")
    proposto = (
        ReferentePropostoOut(nome=membri[proposto_id], sei_tu=proposto_id == user_id)
        if proposto_id in membri
        else None
    )
    if effettivo and effettivo in membri:
        return ReferenteOut(
            tipo="membro", nome=membri[effettivo], sei_tu=effettivo == user_id, proposto=proposto
        )
    return ReferenteOut(
        tipo="titolare", nome=_nome(titolare), sei_tu=user_id == owner_id, proposto=proposto
    )


def _dati_profilo(riga: dict) -> ProfiloPartnerDati:
    if not riga:
        return ProfiloPartnerDati()
    valori = {
        campo: riga[campo]
        for campo in ProfiloPartnerDati.model_fields
        if campo != "esperienze" and riga.get(campo) is not None
    }
    esperienze = []
    for voce in riga.get("esperienze") or []:
        try:
            esperienze.append(EsperienzaPartnerOut.model_validate(voce))
        except ValidationError:
            continue
    return ProfiloPartnerDati(**valori, esperienze=esperienze)


def _competenza_valida(codice) -> bool:
    return isinstance(codice, str) and codice in voc.COMPETENZE


def _bozza_out(riga: dict) -> BozzaAiOut | None:
    stato = riga.get("bozza_ai_stato")
    if stato not in ("in_corso", "pronta", "errore"):
        return None
    proposta = None
    bozza = riga.get("bozza_ai")
    if stato == "pronta" and isinstance(bozza, dict):
        dati = {k: bozza.get(k) for k in ("descrizione_competenze", "competenze", "motivazioni")}
        # Solo i codici del vocabolario di OGGI: una bozza salvata prima di un
        # cambio di vocabolario non espone (né fa applicare) codici tolti.
        if isinstance(dati["competenze"], list):
            dati["competenze"] = [c for c in dati["competenze"] if _competenza_valida(c)]
        if isinstance(dati["motivazioni"], list):
            dati["motivazioni"] = [
                m for m in dati["motivazioni"]
                if not isinstance(m, dict) or _competenza_valida(m.get("codice"))
            ]
        try:
            proposta = BozzaProfiloAiOut.model_validate(dati)
        except ValidationError:
            logger.warning("partner: bozza AI non leggibile (azienda %s)",
                           riga.get("company_profile_id"))
    return BozzaAiOut(
        stato=stato,
        avviata_at=riga.get("bozza_ai_avviata_at"),
        pronta_at=riga.get("bozza_ai_at") if stato == "pronta" else None,
        errore=(
            _MESSAGGI_BOZZA.get(riga.get("bozza_ai_errore") or "", _MESSAGGIO_BOZZA)
            if stato == "errore"
            else None
        ),
        proposta=proposta,
    )


def _voce(elemento, campo: str):
    if isinstance(elemento, dict):
        return elemento.get(campo)
    return getattr(elemento, campo, None)


def _testi_liberi(profilo) -> list[tuple[str, str]]:
    """(etichetta del campo, testo) di tutti i testi liberi del profilo: una
    riga del DB o un `PartnerProfileIn`."""
    testi: list[tuple[str, str]] = []

    def aggiungi(campo: str, valore) -> None:
        if isinstance(valore, str) and valore.strip():
            testi.append((_ETICHETTE_CAMPI[campo], valore))

    aggiungi("descrizione_competenze", _voce(profilo, "descrizione_competenze"))
    for voce in _voce(profilo, "competenze_libere") or []:
        aggiungi("competenze_libere", voce)
    for esperienza in _voce(profilo, "esperienze") or []:
        aggiungi("programma", _voce(esperienza, "programma"))
        aggiungi("titolo", _voce(esperienza, "titolo"))
    for voce in _voce(profilo, "certificazioni") or []:
        aggiungi("certificazioni", voce)
    aggiungi("infrastrutture", _voce(profilo, "infrastrutture"))
    return testi


def _rilievi(profilo, ident: Identificativi | None, *, anonima: bool):
    for etichetta, testo in _testi_liberi(profilo):
        for rilievo in trova_rilievi(testo, ident, anonima=anonima):
            yield etichetta, rilievo, ETICHETTE_RILIEVO.get(rilievo.tipo, "un dato non ammesso")


def testi_senza_invisibili(dati: PartnerProfileIn) -> PartnerProfileIn:
    """Il profilo con i testi liberi senza caratteri di formato invisibili
    (`senza_invisibili`), come i testi delle call: nel DB, e quindi verso
    terzi, non finisce mai un carattere invisibile; i caratteri visibili
    restano quelli dell'utente (la forma canonica serve solo ai controlli).
    Se un testo cambia lo schema si riapplica: spazi ai bordi, voci rimaste
    vuote scartate, doppioni e obblighi (un programma fatto solo di caratteri
    invisibili è un programma mancante)."""
    cambiato = False

    def pulisci(valore):
        nonlocal cambiato
        if not isinstance(valore, str):
            return valore
        pulito = senza_invisibili(valore)
        cambiato = cambiato or pulito != valore
        return pulito

    grezzo = dati.model_dump()
    for campo in ("descrizione_competenze", "infrastrutture"):
        grezzo[campo] = pulisci(grezzo[campo])
    for campo in ("competenze_libere", "certificazioni"):
        grezzo[campo] = [pulisci(voce) for voce in grezzo[campo]]
    for esperienza in grezzo["esperienze"]:
        for campo in ("programma", "titolo"):
            esperienza[campo] = pulisci(esperienza[campo])
    return PartnerProfileIn.model_validate(grezzo) if cambiato else dati


def controlla_testi(profilo, ident: Identificativi | None, *, anonima: bool) -> None:
    """Nessun contatto prima dell'accettazione, in ogni profilo; se anonimo,
    nemmeno gli identificativi dell'azienda. Il primo rilievo bloccante →
    400 `testo_non_conforme`: nomina campo e tipo, MAI il dato."""
    for etichetta, rilievo, tipo in _rilievi(profilo, ident, anonima=anonima):
        if rilievo.bloccante:
            raise AppError(
                400,
                "testo_non_conforme",
                f"{etichetta} contiene {tipo}: toglilo e salva di nuovo. Prima "
                "dell'accettazione non si condividono contatti"
                + (" né dati che identificano l'azienda" if anonima else ""),
            )


def _avvisi_anonimato(ctx: _Contesto) -> list[str]:
    """Avvisi non bloccanti dei profili anonimi (cognomi delle persone del
    registro): li decide il titolare. Riportano la parola trovata, che è un
    dato della sua azienda, e vanno solo a chi vede già l'azienda."""
    if not ctx.profilo or not ctx.anonimo:
        return []
    avvisi: list[str] = []
    for etichetta, rilievo, tipo in _rilievi(ctx.profilo, ctx.ident, anonima=True):
        if rilievo.bloccante:
            continue
        avviso = (
            f"{etichetta} contiene «{rilievo.estratto}», che è anche {tipo}: "
            "se rende riconoscibile l'azienda, toglilo"
        )
        if avviso not in avvisi:
            avvisi.append(avviso)
    return avvisi


async def _componi(primary, active, user: dict, ctx: _Contesto) -> PartnerProfileOut:
    titolare, membri = await asyncio.gather(
        _titolare(primary, user, ctx.owner_id),
        _membri_con_accesso(primary, ctx.owner_id, ctx.company_id),
    )
    riga = ctx.profilo or {}
    dedotti = _dedotti(ctx)
    # Il referente in carica non si propone di nuovo: la scelta serve a
    # cambiarlo.
    in_carica = str(riga.get("referente_user_id") or "")
    versione = riga.get("consenso_versione")
    consenso_at = riga.get("consenso_at")
    visibile = riga.get("visibile_come_partner") is True
    return PartnerProfileOut(
        editable=bool(active.editable),
        esiste=ctx.profilo is not None,
        visibile=visibile,
        anonimo=ctx.anonimo,
        sospeso=riga.get("sospeso_at") is not None,
        consenso=ConsensoStatoOut(versione=versione, at=consenso_at)
        if versione and consenso_at
        else None,
        informativa_versione_corrente=INFORMATIVA_PARTNER_VERSIONE,
        riconsenso_suggerito=visibile and versione != INFORMATIVA_PARTNER_VERSIONE,
        identita=_identita(ctx, titolare),
        profilo=_dati_profilo(riga),
        tipi_soggetto_dedotti=dedotti,
        completezza=completezza(riga, dedotti),
        avvisi_anonimato=_avvisi_anonimato(ctx),
        referente=_referente(riga, user, ctx.owner_id, titolare, membri),
        referenti_possibili=[
            ReferentePossibileOut(user_id=uid, nome=nome)
            for uid, nome in sorted(membri.items(), key=lambda voce: voce[1].casefold())
            if uid != in_carica
        ]
        if active.editable
        else [],
        bozza_ai=_bozza_out(riga),
        vocabolario_versione=riga.get("vocabolario_versione") or voc.VOCABOLARIO_VERSIONE,
        aggiornato_at=riga.get("updated_at"),
    )


# ------------------------------------------------------------------ letture


async def get_profilo(primary, secondary, active, user: dict) -> PartnerProfileOut:
    """Profilo partner dell'azienda attiva (titolare e membri con visibilità),
    con il failsafe delle bozze AI orfane."""
    ctx = await _contesto(primary, active)
    await _failsafe_bozza(primary, ctx)
    return await _componi(primary, active, user, ctx)


async def anteprima(primary, secondary, active, user: dict) -> PartnerPubblicoOut:
    """«Come ti vedono»: la stessa proiezione a whitelist che vedranno le
    altre aziende, anche se il profilo non è visibile (o non è mai stato
    salvato: allora con i soli dati del registro)."""
    ctx = await _contesto(primary, active)
    esercizi = await bilanci_service.carica_esercizi(primary, ctx.company_id)
    lookups = await _lookups_opzionali(secondary)
    riga = ctx.profilo or {"codice_pubblico": CODICE_PUBBLICO_ASSENTE, "anonimo": True}
    return profilo_pubblico(
        riga, ctx.company_data, ctx.dossier, calcola_fasce(esercizi), lookups, ident=ctx.ident
    )


# ------------------------------------------------------------ scritture


def _verifica_lookup(dati: PartnerProfileIn, lookups) -> None:
    """Gli id del profilo devono esistere nelle lookup del catalogo (nessuna
    FK tra database)."""
    controlli = (
        (dati.settori_interesse, lookups.settori, "Settore non riconosciuto"),
        (dati.regioni_interesse, lookups.regioni, "Regione non riconosciuta"),
        (dati.categorie_bando_escluse, lookups.tipologie_bando,
         "Categoria di bando non riconosciuta"),
    )
    for valori, voci, messaggio in controlli:
        ammessi = {voce.id for voce in voci}
        if any(v not in ammessi for v in valori):
            raise BadRequestError(messaggio)
    programmi = {voce.id for voce in lookups.programmi}
    if any(e.programma_id is not None and e.programma_id not in programmi
           for e in dati.esperienze):
        raise BadRequestError("Programma di un'esperienza non riconosciuto")


def payload_upsert(dati: PartnerProfileIn, active, user: dict, punteggio: int) -> dict:
    """Riga dell'upsert: SOLO i campi liberi, le chiavi (azienda e titolare,
    uguali a quelle esistenti) e i campi calcolati. Mai un campo protetto."""
    liberi = dati.model_dump(mode="json")
    payload = {campo: liberi[campo] for campo in CAMPI_LIBERI}
    payload.update(
        company_profile_id=str(active.company_id),
        family_parent_id=str(active.owner_id),
        completezza=punteggio,
        vocabolario_versione=voc.VOCABOLARIO_VERSIONE,
        updated_by=str(user["id"]),
    )
    if CAMPI_PROTETTI & payload.keys():  # pragma: no cover — difesa in profondità
        raise RuntimeError("campo protetto nell'upsert del profilo partner")
    return payload


async def salva_profilo(
    primary, secondary, active, user: dict, dati: PartnerProfileIn
) -> PartnerProfileOut:
    """PUT del profilo (titolare): testi senza caratteri invisibili, lookup,
    controlli dei testi (sulla forma canonica), upsert a whitelist,
    completezza ricalcolata."""
    _richiedi_titolare(active)
    _richiedi_azienda(active)
    dati = testi_senza_invisibili(dati)
    if (
        dati.settori_interesse
        or dati.regioni_interesse
        or dati.categorie_bando_escluse
        or any(e.programma_id is not None for e in dati.esperienze)
    ):
        # Fail-closed: senza catalogo gli id non si verificano e non si salva.
        _verifica_lookup(dati, await lookup_service.get_lookups(secondary))
    ctx = await _contesto(primary, active)
    controlla_testi(dati, ctx.ident, anonima=ctx.anonimo)
    payload = payload_upsert(dati, active, user, completezza(dati, _dedotti(ctx)))
    try:
        await primary.table("company_partner_profiles").upsert(
            payload, on_conflict="company_profile_id"
        ).execute()
    except APIError as exc:
        # Mai il detail nei log: una violazione di vincolo riporta la riga.
        logger.error("partner: salvataggio del profilo non riuscito (code=%s)", exc.code)
        raise UpstreamError() from exc
    partenariato_indice.invalida()  # WP6: il matching usa il profilo salvato
    ctx.profilo = await _profilo(primary, ctx.company_id)
    return await _componi(primary, active, user, ctx)


async def consenso(
    primary, secondary, active, user: dict, dati: ConsensoIn
) -> PartnerProfileOut:
    """Concessione, revoca o cambio di anonimato (titolare) via
    `fn_partner_consenso`.

    Il nominativo (`anonimo=false`) oggi non è disponibile (409
    `nominativo_non_disponibile`, vedi `NOMINATIVO_DISPONIBILE`).
    L'informativa inviata deve essere quella corrente per concedere e per
    passare al nominativo (409 `informativa_superata`); per passare al
    nominativo un profilo già visibile, anche il consenso REGISTRATO deve
    essere su quella versione, altrimenti si passa da una nuova concessione
    con l'informativa mostrata. La revoca e il ritorno all'anonimato non si
    bloccano mai per un'informativa vecchia e registrano la versione del
    consenso esistente, mai quella inviata dal client. Prima di concedere, o
    di tornare anonimi, i testi salvati si ricontrollano con gli
    identificativi (400 `testo_non_conforme`)."""
    _richiedi_titolare(active)
    company_id = _richiedi_azienda(active)
    nominativo = dati.anonimo is False and dati.azione in ("concedi", "anonimato")
    if nominativo and not NOMINATIVO_DISPONIBILE:
        raise AppError(409, "nominativo_non_disponibile", MSG_NOMINATIVO_NON_DISPONIBILE)
    corrente = dati.informativa_versione == INFORMATIVA_PARTNER_VERSIONE
    if not corrente and (dati.azione == "concedi" or nominativo):
        raise _informativa_superata()
    if nominativo and dati.azione == "anonimato":
        # Il consenso registrato può essere su un'informativa superata: il
        # passaggio al nome vale come consenso nuovo solo se l'utente ha
        # riletto il testo corrente (banner «Rileggi e conferma»).
        riga = await _profilo(primary, company_id) or {}
        if (
            riga.get("visibile_come_partner") is True
            and riga.get("consenso_versione") != INFORMATIVA_PARTNER_VERSIONE
        ):
            raise _informativa_superata()
    if dati.anonimo is not None and (
        dati.azione == "concedi" or (dati.azione == "anonimato" and dati.anonimo)
    ):
        ctx = await _contesto(primary, active)
        controlla_testi(ctx.profilo or {}, ctx.ident, anonima=bool(dati.anonimo))
    try:
        await primary.rpc(
            "fn_partner_consenso",
            {
                "p_owner": str(active.owner_id),
                "p_company": company_id,
                "p_attore": str(user["id"]),
                "p_azione": dati.azione,
                # Revoca e anonimato registrano la versione del consenso
                # esistente (la RPC la prende dal profilo).
                "p_versione": dati.informativa_versione if dati.azione == "concedi" else None,
                "p_origine": dati.origine,
                "p_anonimo": dati.anonimo,
                "p_richiedi_non_sandbox": richiedi_non_sandbox(),
            },
        ).execute()
    except APIError as exc:
        raise_from_rpc(exc)
    # WP6 (M2, M4): la visibilità cambia il matching; le chiavi dei
    # collegamenti si calcolano per chi entra e si cancellano per chi esce
    # senza call non chiuse. Best-effort: mai un errore al consenso.
    if dati.azione == "concedi":
        await partenariato_indice.ricostruisci_collegamenti(primary, company_id)
    elif dati.azione == "revoca":
        await partenariato_indice.rimuovi_collegamenti_se_non_idonea(primary, company_id)
    else:
        partenariato_indice.invalida()
    return await get_profilo(primary, secondary, active, user)


async def referente(
    primary, secondary, active, user: dict, dati: ReferenteIn
) -> PartnerProfileOut:
    """Il titolare propone un membro (che deve accettare), annulla la sola
    proposta pendente (`annulla_proposta`: il referente in carica resta) o
    toglie il referente; proporre sé stesso riporta il referente al
    titolare."""
    _richiedi_titolare(active)
    company_id = _richiedi_azienda(active)
    try:
        await primary.rpc(
            "fn_partner_referente",
            {
                "p_owner": str(active.owner_id),
                "p_company": company_id,
                "p_attore": str(user["id"]),
                "p_azione": dati.azione,
                "p_user": str(dati.user_id) if dati.user_id else None,
                # Solo ripiego del registro: la revoca registra la versione
                # accettata dal referente.
                "p_versione": INFORMATIVA_REFERENTE_VERSIONE,
            },
        ).execute()
    except APIError as exc:
        raise_from_rpc(exc)
    return await get_profilo(primary, secondary, active, user)


async def risposta_referente(
    primary, secondary, active, user: dict, dati: ReferenteRispostaIn
) -> PartnerProfileOut:
    """Il membro proposto accetta (con l'informativa del referente corrente)
    o rifiuta; il referente può rinunciare (`revoca`). Non serve essere
    titolare: la RPC verifica che l'attore sia la persona giusta."""
    company_id = _richiedi_azienda(active)
    corrente = dati.informativa_versione == INFORMATIVA_REFERENTE_VERSIONE
    if dati.azione == "accetta" and not corrente:
        raise _informativa_superata(referente=True)
    try:
        await primary.rpc(
            "fn_partner_referente",
            {
                "p_owner": str(active.owner_id),
                "p_company": company_id,
                "p_attore": str(user["id"]),
                "p_azione": dati.azione,
                "p_user": None,
                "p_versione": dati.informativa_versione if corrente else None,
            },
        ).execute()
    except APIError as exc:
        raise_from_rpc(exc)
    return await get_profilo(primary, secondary, active, user)


async def scarta_bozza(primary, secondary, active, user: dict) -> PartnerProfileOut:
    """Scarta la proposta AI (pronta o in errore). Una bozza in preparazione
    non si scarta (409 `bozza_in_corso`): il job la chiuderà comunque."""
    _richiedi_titolare(active)
    company_id = _richiedi_azienda(active)
    await primary.table("company_partner_profiles").update(
        {"bozza_ai": None, "bozza_ai_stato": None, "bozza_ai_errore": None,
         "bozza_ai_at": None, "bozza_ai_avviata_at": None}
    ).eq("company_profile_id", company_id).in_("bozza_ai_stato", ["pronta", "errore"]).execute()
    riga = await _profilo(primary, company_id)
    if riga and riga.get("bozza_ai_stato") == "in_corso":
        raise AppError(409, "bozza_in_corso", "La bozza è ancora in preparazione")
    return await get_profilo(primary, secondary, active, user)


# ------------------------------------------------------------ bozza AI


def stima_riserva_cents(messaggio: str) -> int:
    """Riserva al caso peggiore della bozza: prompt di sistema + messaggio +
    schema e tutto l'output consentito (2,5 caratteri per token)."""
    settings = get_settings()
    return stima_cents(
        settings.partenariato_ai_model,
        len(SYSTEM_PROFILO) + len(messaggio) + len(schema_json()),
        settings.partner_bozza_ai_max_tokens,
    )


def _descrizioni_ateco(lookups) -> dict[str, str]:
    descrizioni: dict[str, str] = {}
    for voce in getattr(lookups, "codici_ateco", None) or []:
        chiave = re.sub(r"\D", "", voce.codice or "")
        if chiave and voce.descrizione:
            descrizioni.setdefault(chiave, voce.descrizione)
    return descrizioni


async def avvia_bozza_ai(primary, secondary, ai, active, user: dict) -> PartnerProfileOut:
    """Prenota (fail-closed) e avvia in background la bozza AI di descrizione
    e competenze. Errori: 403, 400 `dati_insufficienti` (nessun ATECO dal
    registro), 503 `ai_not_configured`, 409 `bozza_in_corso`, 429
    `ai_limite_giornaliero` (per azienda o per titolare) / `ai_sospesa_oggi`."""
    _richiedi_titolare(active)
    ctx = await _contesto(primary, active)
    derived = (ctx.company_data or {}).get("derived") or {}
    dossier = ctx.dossier
    ateco = derived.get("ateco_principale") or (
        (dossier.get("attivita") or {}).get("ateco") or {}
    ).get("codice")
    if ctx.company_data is None or not ateco:
        raise AppError(
            400,
            "dati_insufficienti",
            "Per preparare la bozza servono i dati ufficiali dell'azienda: importali prima "
            "dalla partita IVA",
        )
    if not ai.enabled:
        raise AiNotConfiguredError(MSG_AI_NON_CONFIGURATA)

    settings = get_settings()
    ident = ctx.ident
    messaggio = build_profilo_input(
        derived=derived,
        dossier=dossier,
        descrizione=(ctx.profilo or {}).get("descrizione_competenze"),
        ident=ident,
        people=ctx.persone,
        descrizioni_ateco=_descrizioni_ateco(await _lookups_opzionali(secondary)),
    )
    riserva = stima_riserva_cents(messaggio)
    try:
        resp = await primary.rpc(
            "fn_partner_bozza_ai_prenota",
            {
                "p_owner": ctx.owner_id,
                "p_company": ctx.company_id,
                "p_richiedente": str(user["id"]),
                "p_budget_cents": budget_cents_gruppo("altri"),
                "p_costo_riservato_cents": riserva,
                "p_limite_azienda": settings.partner_bozza_ai_limite_giorno,
                # Solo il titolare avvia la bozza: il limite per richiedente
                # è il limite del titolare su tutte le sue aziende.
                "p_limite_richiedente": settings.partner_bozza_ai_limite_utente_giorno,
            },
        ).execute()
    except APIError as exc:
        if (exc.details or "").strip() == "ai_limite_utente":
            # La mappa comune parla di «analisi» (WP3): qui sono bozze.
            raise AppError(429, "ai_limite_giornaliero", MSG_LIMITE_BOZZE_UTENTE) from exc
        raise_from_rpc(exc)
    esecuzione_id = resp.data
    if not isinstance(esecuzione_id, str) or not esecuzione_id:
        # Senza un'esecuzione registrata la spesa non sarebbe contata.
        raise UpstreamError()

    _spawn(
        _esegui_bozza(
            primary,
            ai,
            esecuzione_id=esecuzione_id,
            company_id=ctx.company_id,
            owner_id=ctx.owner_id,
            user_id=str(user["id"]),
            messaggio=messaggio,
            riserva_cents=riserva,
            ident=ident,
        )
    )
    # Il job è già partito: una rilettura fallita non deve lasciarlo senza
    # risposta coerente.
    try:
        ctx.profilo = await _profilo(primary, ctx.company_id)
    except Exception:
        logger.exception("partner: rilettura dopo la prenotazione della bozza non riuscita")
        ctx.profilo = None
    if not ctx.profilo or ctx.profilo.get("bozza_ai_stato") != "in_corso":
        ctx.profilo = {
            **(ctx.profilo or {}),
            "bozza_ai_stato": "in_corso",
            "bozza_ai_avviata_at": _adesso().isoformat(),
            "bozza_ai_esecuzione_id": esecuzione_id,
        }
    return await _componi(primary, active, user, ctx)


@dataclass
class _Chiusura:
    """Una chiusura del job: bozza, esecuzione nel registro unico della spesa
    e riga del registro consumi da scrivere se l'esecuzione la chiude lei."""

    bozza_stato: str  # pronta | errore
    stato: str  # stato finale dell'esecuzione
    costo: int | None  # None = costo ignoto: la riserva resta nel budget
    costo_registro: int  # api_usage_events: mai ignoto (la riserva, al peggio)
    outcome: str  # success | error | timeout_unknown
    meta: dict
    bozza: dict | None = None
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
    chiusura: _Chiusura | None = None  # ultima chiusura tentata
    esito: dict | None = None  # risposta della chiusura atomica, se è tornata
    registrata: bool = False  # riga del registro consumi scritta o tentata


async def _chiudi(primary, job: _Job, *, company_id: str, esecuzione_id: str,
                  chiusura: _Chiusura) -> dict | None:
    """Chiusura ATOMICA (`fn_partner_bozza_ai_concludi`): bozza (solo se è
    ancora quella di questa esecuzione e in corso: il failsafe, uno scarto o
    una nuova bozza vincono) ed esecuzione nella stessa transazione. Se non
    riesce restano in corso entrambe e le chiude il failsafe, che registra lui
    il consumo. Ritorna `{bozza_scritta, esecuzione_chiusa}` o None."""
    job.chiusura, job.esito = chiusura, None
    try:
        resp = await primary.rpc(
            "fn_partner_bozza_ai_concludi",
            {
                "p_company": company_id,
                "p_esecuzione_id": esecuzione_id,
                "p_bozza_stato": chiusura.bozza_stato,
                "p_bozza": chiusura.bozza,
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
        logger.error("partner: chiusura della bozza non riuscita (azienda %s, %s)",
                     company_id, type(exc).__name__)
        return None
    job.esito = resp.data if isinstance(resp.data, dict) else {}
    return job.esito


async def _registra(
    primary, *, user_id: str, owner_id: str, outcome: str, cost_cents: int, meta: dict
) -> None:
    """Registro consumi (`api_usage_events`); non solleva mai."""
    await record_usage(
        primary,
        user_id=user_id,
        family_parent_id=owner_id,
        service=SERVIZIO,
        outcome=outcome,
        cost_cents=int(cost_cents),
        meta=meta,
        provider="anthropic",
    )


async def _registra_chiusura(primary, job: _Job, *, user_id: str, owner_id: str) -> None:
    """Registro consumi dell'ultima chiusura, SOLO se ha chiuso lei
    l'esecuzione (altrimenti l'ha chiusa e registrata il failsafe) e mai due
    volte: il flag si alza PRIMA dell'insert, così una cancellazione a metà
    insert non lo ripete."""
    chiusura, esito = job.chiusura, job.esito
    if job.registrata or chiusura is None or not esito or not esito.get("esecuzione_chiusa"):
        return
    job.registrata = True
    outcome, meta = chiusura.outcome, chiusura.meta
    if chiusura.bozza_stato == "pronta" and not esito.get("bozza_scritta"):
        # Il failsafe (o uno scarto) ha chiuso la bozza durante la chiamata:
        # il risultato pagato va perso, la spesa no.
        outcome, meta = "error", {**meta, "esito": "superata"}
    await _registra(primary, user_id=user_id, owner_id=owner_id, outcome=outcome,
                    cost_cents=chiusura.costo_registro, meta=meta)


async def _esegui_bozza(
    primary,
    ai,
    *,
    esecuzione_id: str,
    company_id: str,
    owner_id: str,
    user_id: str,
    messaggio: str,
    riserva_cents: int,
    ident: Identificativi | None,
) -> str:
    """Chiamata al modello → post-elaborazione → chiusura atomica di bozza ed
    esecuzione → registro consumi. Non solleva MAI (salvo la cancellazione
    del task, dopo aver chiuso e registrato ciò che mancava).

    Costi come il WP3: modello non chiamato → 0 esplicito; risposta arrivata
    ma errore → max(reale, riserva); timeout → riserva; errore di rete senza
    usage → costo ignoto (None: la riserva resta nel budget). Ritorna
    l'esito (pronta, superata, timeout, errore)."""
    settings = get_settings()
    modello = settings.partenariato_ai_model
    meta = {
        "company_profile_id": company_id,
        "esecuzione_id": esecuzione_id,
        "model": modello,
        "prompt_version": PROFILO_PROMPT_VERSION,
    }
    job = _Job()

    async def chiudi(chiusura: _Chiusura) -> dict | None:
        esito = await _chiudi(primary, job, company_id=company_id,
                              esecuzione_id=esecuzione_id, chiusura=chiusura)
        await _registra_chiusura(primary, job, user_id=user_id, owner_id=owner_id)
        return esito

    try:
        try:
            job.inviata = True  # la richiesta al modello può essere partita
            bozza, usage = await ai.genera(
                SYSTEM_PROFILO,
                messaggio,
                BozzaProfiloAi,
                model=modello,
                max_tokens=settings.partner_bozza_ai_max_tokens,
                timeout=settings.partner_bozza_ai_timeout_seconds,
            )
            job.usage = usage
            costo = costo_cents(modello, usage.input_tokens, usage.output_tokens)
            proposta = pulisci_bozza(bozza, ident)
            esito = await chiudi(_Chiusura(
                bozza_stato="pronta", bozza=proposta, stato="conclusa", costo=costo,
                costo_registro=costo, outcome="success",
                meta={**meta, "esito": "pronta", "input_tokens": usage.input_tokens,
                      "output_tokens": usage.output_tokens},
                input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                model=modello,
            ))
            if esito is None:
                return "errore"  # restano in corso: failsafe
            if not esito.get("bozza_scritta"):
                logger.warning("partner: bozza superata durante la generazione (azienda %s)",
                               company_id)
                return "superata"
            return "pronta"
        except AiTimeoutError:
            # Esito e addebito ignoti: si registra il caso peggiore, la riserva.
            await chiudi(_Chiusura(
                bozza_stato="errore", bozza_errore="timeout", stato="timeout",
                costo=riserva_cents, costo_registro=riserva_cents, outcome="timeout_unknown",
                meta={**meta, "esito": "timeout"}, model=modello, errore="timeout",
            ))
            return "timeout"
        except AiUpstreamError as exc:
            uso = exc.usage
            if uso is not None:
                # La risposta è arrivata (troncata o non valida): pagata.
                costo = max(costo_cents(modello, uso.input_tokens, uso.output_tokens),
                            riserva_cents)
                codice = "ai_risposta_non_valida"
            else:
                costo = None  # errore di rete o del provider: costo ignoto
                codice = "ai_non_disponibile"
            await chiudi(_Chiusura(
                bozza_stato="errore", bozza_errore=codice, stato="errore", costo=costo,
                costo_registro=riserva_cents if costo is None else costo, outcome="error",
                meta={**meta, "esito": "errore", "errore": codice,
                      "costo_ignoto": costo is None},
                input_tokens=uso.input_tokens if uso else 0,
                output_tokens=uso.output_tokens if uso else 0, model=modello, errore=codice,
            ))
            return "errore"
        except Exception as exc:
            logger.error("partner: bozza AI non riuscita (azienda %s, %s)", company_id,
                         type(exc).__name__)
            usage = job.usage
            if usage is not None:
                # Chiamata riuscita e pagata, guasto dopo: max(reale, riserva).
                costo = max(costo_cents(modello, usage.input_tokens, usage.output_tokens),
                            riserva_cents)
            elif job.inviata and not isinstance(exc, AiNotConfiguredError):
                costo = None
            else:
                costo = 0  # il modello non è stato chiamato
            await chiudi(_Chiusura(
                bozza_stato="errore", bozza_errore="errore_interno", stato="errore",
                costo=costo, costo_registro=riserva_cents if costo is None else costo,
                outcome="error", meta={**meta, "esito": "errore", "costo_ignoto": costo is None},
                input_tokens=usage.input_tokens if usage else 0,
                output_tokens=usage.output_tokens if usage else 0,
                model=modello if costo != 0 else None, errore="errore_interno",
            ))
            return "errore"
    except asyncio.CancelledError:
        # Task cancellato (spegnimento, deploy) in QUALUNQUE punto, anche
        # dentro un ramo d'errore: si completa ciò che manca, poi si rilancia.
        await _chiudi_su_cancellazione(
            primary, job, company_id=company_id, esecuzione_id=esecuzione_id, user_id=user_id,
            owner_id=owner_id, modello=modello, riserva_cents=riserva_cents, meta=meta,
        )
        raise


async def _chiudi_su_cancellazione(
    primary,
    job: _Job,
    *,
    company_id: str,
    esecuzione_id: str,
    user_id: str,
    owner_id: str,
    modello: str,
    riserva_cents: int,
    meta: dict,
) -> None:
    """Chiusura best-effort di una bozza il cui task è stato cancellato, con
    un tempo massimo (allo spegnimento non si trattiene il processo), senza
    ripetere i passi già tentati dal job:
    - registro già scritto (o tentato) → niente;
    - chiusura del job già tornata → si registra la sua, se l'ha chiusa lei;
    - altrimenti si chiude come interrotta: la RPC è idempotente, quindi se la
      chiusura in volo del job era arrivata questa non chiude nulla e non
      registra. Se non riesce nulla, ci pensa il failsafe."""
    if job.registrata:
        return
    usage = job.usage
    if usage is not None:
        costo = max(costo_cents(modello, usage.input_tokens, usage.output_tokens), riserva_cents)
        outcome, tokens = "error", (usage.input_tokens, usage.output_tokens)
    elif job.inviata:
        # Chiamata forse partita e forse addebitata: come un timeout.
        costo, outcome, tokens = riserva_cents, "timeout_unknown", (0, 0)
    else:
        costo, outcome, tokens = 0, "error", (0, 0)
    try:
        async with asyncio.timeout(CHIUSURA_SU_CANCELLAZIONE_SECONDI):
            if job.esito is None:
                await _chiudi(primary, job, company_id=company_id, esecuzione_id=esecuzione_id,
                              chiusura=_Chiusura(
                                  bozza_stato="errore", bozza_errore="interrotta",
                                  stato="interrotta", costo=costo, costo_registro=costo,
                                  outcome=outcome, meta={**meta, "esito": "interrotta"},
                                  input_tokens=tokens[0], output_tokens=tokens[1],
                                  model=modello if costo else None, errore="interrotta",
                              ))
            await _registra_chiusura(primary, job, user_id=user_id, owner_id=owner_id)
    except Exception:  # noqa: BLE001 — il failsafe chiuderà la bozza
        logger.warning("partner: chiusura dopo la cancellazione non riuscita (azienda %s)",
                       company_id)
