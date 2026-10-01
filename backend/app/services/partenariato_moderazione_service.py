"""Moderazione DSA dei partenariati (WP9, docs/partenariati.md W2, T3, T8,
Q22; DSA art. 16-20).

La segnalazione nasce in `partner_call_service.segnala` (WP5, esteso ai
messaggi nel WP7: si segnala solo ciò che si vede, con lo snapshot e la
conferma di ricezione). Qui il resto del ciclo, e ogni effetto su call,
profili e messaggi passa SOLO dalle RPC della 0041 (nessuna scrittura
diretta da Python sugli stati di altri moduli):

- coda dell'admin, presa in carico (`ricevuta → in_esame`), decisione
  motivata con effetto ATOMICO nella RPC (call sospesa con lo stato
  precedente salvato, messaggio oscurato, profilo sospeso, oppure
  `nessuna_azione`) e coerente con il tipo di contenuto;
- statement of reasons (DSA art. 17) dal template versionato di
  `partenariato_moderazione_testi` («BOZZA — DA RIVEDERE CON IL LEGALE»),
  salvato nella segnalazione e inviato all'autore in-app (avviso breve, il
  testo si legge dalla pagina della segnalazione) e per email al titolare
  (solo indirizzi recapitabili: è un avviso obbligatorio, non si
  disattiva dalle preferenze);
- esito comunicato a chi ha segnalato, senza mai rivelarne l'identità
  all'autore (e senza rivelare l'autore a chi ha segnalato);
- ricorso interno (art. 20): uno solo, entro 6 mesi dalla decisione, del
  titolare dell'azienda autrice contro una restrizione o di chi ha
  segnalato contro `nessuna_azione`; deciso dall'admin con motivazione
  (`riformata` annulla la restrizione, o la applica se la decisione era
  `nessuna_azione`);
- sospensione e ripristino diretti dell'admin, con lo statement of reasons
  «d'ufficio» all'autore: in-app nella forma breve (motivazione per intero,
  al massimo 500 caratteri), per email il testo completo, che resta anche
  nell'audit;
- contesto per l'admin di un messaggio segnalato: finestra di ±10 messaggi;
  la conversazione intera solo con una motivazione. Ogni lettura scrive
  PRIMA l'audit (`moderazione.contesto_accessed` o
  `moderazione.contesto_completo`), fail-closed: senza audit niente dati.

Chi vede cosa: l'admin tutto il contenuto segnalato ma nessun id di utente;
chi ha segnalato la propria segnalazione e l'esito; l'azienda autrice (con
l'azienda attiva, membri in lettura) solo le segnalazioni che l'hanno
riguardata con una restrizione, mai la descrizione né lo snapshot. Fuori da
questi casi 404, come una segnalazione inesistente.

Log: solo id e codici, mai testi, nomi o email.
"""

import asyncio
import logging
import uuid
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

from postgrest.exceptions import APIError

from app.core.config import get_settings
from app.core.errors import AppError, ForbiddenError, NotFoundError, UpstreamError
from app.schemas.common import Page
from app.schemas.partenariato_moderazione import (
    MSG_MOTIVAZIONE,
    AutoreAdminOut,
    ContestoOut,
    DecisioneIn,
    MessaggioContestoOut,
    RicorsoDecisioneIn,
    RicorsoIn,
    RicorsoOut,
    SegnalazioneAdminOut,
    SegnalazioneEsitoOut,
    SospendiIn,
    SospensioneIn,
    SospensioneOut,
    StatementOut,
    testo_limitato,
)
from app.services import (
    bando_alert_service,
    email_service,
    partenariato_indice,
    partenariato_notifiche,
)
from app.services import partenariato_moderazione_testi as testi
from app.services.notification_service import notify
from app.services.paginazione import pagina
from app.services.partenariato_errori import RPC_ERRORS, raise_from_rpc

logger = logging.getLogger("bandofit.partenariati")

SEGNALAZIONE_SELECT = (
    "id,oggetto_tipo,oggetto_id,segnalante_user_id,motivo,descrizione,contenuto_snapshot,stato,"
    "autore_company_profile_id,decisione,motivazione,sor_testo,deciso_at,ricorso_testo,"
    "ricorso_at,ricorso_esito,ricorso_motivazione,ricorso_deciso_at,created_at"
)
MESSAGGIO_SELECT = (
    "id,conversazione_id,mittente_company_profile_id,testo,nascosto_moderazione_at,created_at"
)
CONVERSAZIONE_SELECT = "id,company_creatore_id,company_partner_id"

STATI_APERTI: tuple[str, ...] = ("ricevuta", "in_esame", "ricorso_presentato")
# Finestra del contesto attorno al messaggio segnalato e tetto della
# conversazione intera (letta a pagine sotto il max-rows 1000).
CONTESTO_FINESTRA = 10
CONTESTO_COMPLETO_MAX = 5000
_PAGINA = 1000
_BLOCCO = 100

AUDIT_CONTESTO = "moderazione.contesto_accessed"
AUDIT_CONTESTO_COMPLETO = "moderazione.contesto_completo"
AUDIT_STATEMENT = "moderazione.statement_inviato"

MSG_NON_TROVATA = "Segnalazione non trovata"
MSG_SOLO_TITOLARE = "Il ricorso lo presenta il titolare dell'azienda"
MSG_CONTESTO_SOLO_MESSAGGI = "Il contesto della conversazione c'è solo per i messaggi segnalati"
MSG_MESSAGGIO_SPARITO = "Il messaggio segnalato non esiste più"
MSG_OGGETTO_NON_TROVATO = "Contenuto non trovato"

_background_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    """Email in background (sostituibile nei test): la decisione è già
    avvenuta e la notifica in-app è il canale affidabile."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


# ------------------------------------------------------------------ utilità


def _istante(valore: Any) -> datetime | None:
    if isinstance(valore, datetime):
        return valore if valore.tzinfo else valore.replace(tzinfo=timezone.utc)
    if not isinstance(valore, str):
        return None
    try:
        istante = datetime.fromisoformat(valore.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return istante if istante.tzinfo else istante.replace(tzinfo=timezone.utc)


def _id_segnalazione(valore: Any) -> str:
    """Uuid canonico; malformato = segnalazione inesistente (404, mai il
    22P02 → 502)."""
    try:
        return str(uuid.UUID(str(valore).strip()))
    except (ValueError, AttributeError, TypeError):
        raise NotFoundError(MSG_NON_TROVATA) from None


def id_oggetto(oggetto_tipo: str, valore: Any) -> str:
    """Riferimento dell'oggetto come in `partner_segnalazioni.oggetto_id`:
    uuid canonico della call o del `codice_pubblico` del profilo, intero
    positivo per il messaggio. Malformato → 404."""
    testo = str(valore).strip()
    if oggetto_tipo == "messaggio":
        if testo.isdigit() and 0 < len(testo) <= 18 and int(testo) > 0:
            return str(int(testo))
        raise NotFoundError(MSG_OGGETTO_NON_TROVATO)
    try:
        return str(uuid.UUID(testo))
    except ValueError:
        raise NotFoundError(MSG_OGGETTO_NON_TROVATO) from None


def _errore(detail: str) -> AppError:
    return AppError(*RPC_ERRORS[detail])


async def _rpc(primary, nome: str, parametri: dict) -> dict:
    try:
        resp = await primary.rpc(nome, parametri).execute()
    except APIError as exc:
        raise_from_rpc(exc)
    dati = resp.data
    if not isinstance(dati, dict):
        logger.error("moderazione: risposta inattesa da %s", nome)
        raise UpstreamError()
    return dati


async def _una(query) -> dict | None:
    resp = await query.limit(1).execute()
    riga = resp.data[0] if resp.data else None
    return riga if isinstance(riga, dict) else None


async def _carica(primary, segnalazione_id: Any) -> dict:
    identificativo = _id_segnalazione(segnalazione_id)
    riga = await _una(
        primary.table("partner_segnalazioni").select(SEGNALAZIONE_SELECT)
        .eq("id", identificativo)
    )
    if riga is None:
        raise NotFoundError(MSG_NON_TROVATA)
    return riga


def _blocchi(ids: list[str]) -> Iterable[list[str]]:
    for inizio in range(0, len(ids), _BLOCCO):
        yield ids[inizio : inizio + _BLOCCO]


async def _denominazioni(primary, company_ids: Iterable[Any]) -> dict[str, str | None]:
    """Ragione sociale delle aziende autrici (solo verso l'admin)."""
    ids = sorted({str(c) for c in company_ids if c})
    uscita: dict[str, str | None] = {}
    for blocco in _blocchi(ids):
        resp = await (
            primary.table("company_profiles").select("id,ragione_sociale")
            .in_("id", blocco).execute()
        )
        uscita.update({str(r["id"]): r.get("ragione_sociale") for r in resp.data or []})
    return uscita


def _ricorso_da(riga: Mapping) -> str | None:
    """Chi ha presentato il ricorso: la 0041 lo ammette dall'autore contro
    una restrizione e da chi ha segnalato contro `nessuna_azione`."""
    if riga.get("ricorso_testo") is None:
        return None
    return "segnalante" if riga.get("decisione") == "nessuna_azione" else "autore"


def _autore_coinvolto(riga: Mapping) -> bool:
    """L'azienda autrice sa della segnalazione solo se una restrizione l'ha
    riguardata: decisione restrittiva, o `nessuna_azione` riformata dal
    ricorso di chi ha segnalato. Prima della decisione, o senza effetti su
    di lei, nulla."""
    decisione = riga.get("decisione")
    if decisione and decisione != "nessuna_azione":
        return True
    return decisione == "nessuna_azione" and riga.get("ricorso_esito") == "riformata"


def _ruolo(riga: Mapping, active, user: Mapping) -> str | None:
    if str(riga.get("segnalante_user_id")) == str(user.get("id")):
        return "segnalante"
    company_id = getattr(active, "company_id", None)
    if (company_id and str(riga.get("autore_company_profile_id")) == str(company_id)
            and _autore_coinvolto(riga)):
        return "autore"
    return None


def _statement(riga: Mapping, *, motivazione: str, deciso_at: datetime,
               origine: str) -> str:
    return testi.genera_statement(
        oggetto_tipo=riga["oggetto_tipo"],
        motivazione=motivazione,
        deciso_at=deciso_at,
        origine=origine,
        riferimento=testi.codice_breve(riga["id"]),
        motivo_segnalazione=riga.get("motivo"),
        segnalazione_at=_istante(riga.get("created_at")),
    )


def _statement_autore(riga: Mapping) -> str | None:
    """Lo statement of reasons che l'autore ha ricevuto: quello salvato con
    la decisione, oppure (restrizione nata dal ricorso di chi ha segnalato,
    che la 0041 non salva nella riga) lo stesso testo rigenerato dal
    template, deterministico sugli stessi dati."""
    if riga.get("sor_testo"):
        return riga["sor_testo"]
    deciso_at = _istante(riga.get("ricorso_deciso_at"))
    if (riga.get("decisione") == "nessuna_azione" and riga.get("ricorso_esito") == "riformata"
            and deciso_at and riga.get("ricorso_motivazione")):
        return _statement(riga, motivazione=riga["ricorso_motivazione"], deciso_at=deciso_at,
                          origine="ricorso")
    return None


def _decisione_effettiva(riga: Mapping) -> str | None:
    """La decisione dopo il ricorso: `riformata` la ribalta (una restrizione
    diventa `nessuna_azione`, `nessuna_azione` diventa la restrizione del
    tipo di contenuto, applicata dal ricorso di chi aveva segnalato)."""
    decisione = riga.get("decisione")
    if decisione is None or riga.get("ricorso_esito") != "riformata":
        return decisione
    if decisione == "nessuna_azione":
        return testi.DECISIONE_PER_OGGETTO.get(riga.get("oggetto_tipo"))
    return "nessuna_azione"


def _coerente(riga: Mapping, decisione: str) -> None:
    """Coerenza tra decisione e tipo di contenuto (la ricontrolla la RPC)."""
    if decisione != "nessuna_azione" and decisione != testi.DECISIONE_PER_OGGETTO.get(
            riga.get("oggetto_tipo")):
        raise _errore("decisione_non_valida")


# ------------------------------------------------------------- proiezioni


def _ricorso_out(riga: Mapping, *, con_testo: bool) -> RicorsoOut | None:
    da = _ricorso_da(riga)
    if da is None:
        return None
    return RicorsoOut(
        da=da,
        testo=riga.get("ricorso_testo") if con_testo else None,
        at=riga.get("ricorso_at"),
        esito=riga.get("ricorso_esito"),
        motivazione=riga.get("ricorso_motivazione"),
        deciso_at=riga.get("ricorso_deciso_at"),
    )


def _admin_out(riga: Mapping, denominazioni: Mapping[str, str | None], *,
               effetto: str | None = None) -> SegnalazioneAdminOut:
    deciso_at = _istante(riga.get("deciso_at"))
    autore = riga.get("autore_company_profile_id")
    return SegnalazioneAdminOut(
        id=riga["id"],
        codice=testi.codice_breve(riga["id"]),
        oggetto_tipo=riga["oggetto_tipo"],
        oggetto_id=str(riga["oggetto_id"]),
        motivo=riga["motivo"],
        descrizione=riga.get("descrizione") or "",
        contenuto_snapshot=riga.get("contenuto_snapshot") or {},
        stato=riga["stato"],
        created_at=riga.get("created_at"),
        autore=AutoreAdminOut(company_profile_id=autore,
                              ragione_sociale=denominazioni.get(str(autore)))
        if autore else None,
        decisione=riga.get("decisione"),
        decisione_effettiva=_decisione_effettiva(riga),
        motivazione=riga.get("motivazione"),
        # Anche quello della restrizione nata dal ricorso di chi aveva
        # segnalato (non salvato nella riga: rigenerato, identico).
        sor_testo=_statement_autore(riga),
        deciso_at=deciso_at,
        ricorso_entro=testi.scadenza_ricorso(deciso_at) if deciso_at else None,
        ricorso=_ricorso_out(riga, con_testo=True),
        effetto=effetto,
    )


async def _admin_una(primary, riga: Mapping, *, effetto: str | None = None
                     ) -> SegnalazioneAdminOut:
    autore = riga.get("autore_company_profile_id")
    denominazioni = await _denominazioni(primary, [autore] if autore else [])
    return _admin_out(riga, denominazioni, effetto=effetto)


def _esito_out(riga: Mapping, ruolo: str, *, editable: bool) -> SegnalazioneEsitoOut:
    """La segnalazione per chi ha segnalato o per l'autore (whitelist)."""
    decisione = riga.get("decisione")
    deciso_at = _istante(riga.get("deciso_at"))
    # Chi può ricorrere su questa decisione (regola della 0041).
    titolato = (ruolo == "autore" and decisione not in (None, "nessuna_azione")) or (
        ruolo == "segnalante" and decisione == "nessuna_azione")
    puo_agire = ruolo == "segnalante" or editable
    possibile = (
        titolato and puo_agire and riga.get("stato") == "decisa"
        and riga.get("ricorso_testo") is None and testi.entro_ricorso(deciso_at)
    )
    return SegnalazioneEsitoOut(
        id=riga["id"],
        codice=testi.codice_breve(riga["id"]),
        ruolo=ruolo,
        oggetto_tipo=riga["oggetto_tipo"],
        motivo=riga["motivo"],
        stato=riga["stato"],
        created_at=riga.get("created_at"),
        descrizione=riga.get("descrizione") if ruolo == "segnalante" else None,
        decisione=decisione,
        decisione_effettiva=_decisione_effettiva(riga),
        motivazione=riga.get("motivazione"),
        deciso_at=deciso_at,
        sor_testo=_statement_autore(riga) if ruolo == "autore" else None,
        ricorso=_ricorso_out(riga, con_testo=_ricorso_da(riga) == ruolo),
        ricorso_possibile=possibile,
        ricorso_entro=testi.scadenza_ricorso(deciso_at) if titolato and deciso_at else None,
        editable=puo_agire,
    )


# ------------------------------------------------------------- notifiche


def _url_segnalazione(segnalazione_id: Any, company_id: Any = None) -> str:
    base = f"/app/partenariati/segnalazioni/{segnalazione_id}"
    return f"{base}?azienda={company_id}" if company_id else base


async def _url_oggetto(primary, oggetto_tipo: str, oggetto_id: str, company_id: str
                       ) -> str | None:
    """Dove l'autore ritrova il contenuto di una decisione d'ufficio."""
    if oggetto_tipo == "call":
        return f"/app/partenariati/call/{oggetto_id}?azienda={company_id}"
    if oggetto_tipo == "profilo":
        return f"/app/azienda?azienda={company_id}#partner"
    try:
        msg = await _una(primary.table("partner_messaggi").select("id,conversazione_id")
                         .eq("id", int(oggetto_id)))
    except Exception as exc:  # noqa: BLE001 — solo il link
        logger.warning("moderazione: conversazione del messaggio non letta (%s)",
                       getattr(exc, "code", None) or type(exc).__name__)
        return None
    if msg is None:
        return None
    return f"/app/partenariati/conversazioni/{msg['conversazione_id']}?azienda={company_id}"


def url_assoluto(percorso: str) -> str:
    return f"{get_settings().frontend_url.rstrip('/')}{percorso}"


async def _email_statement(primary, destinatari: list[dict], company_id: str,
                           oggetto_tipo: str, statement: str, percorso: str | None) -> int:
    """Lo statement of reasons per email al titolare dell'azienda autrice:
    solo indirizzi recapitabili (verificati e non soppressi). Mai solleva.
    → email partite."""
    try:
        recapitabili = await bando_alert_service.filtra_recapitabili(primary, destinatari)
        if not recapitabili:
            return 0
        azienda = await _una(primary.table("company_profiles").select("id,ragione_sociale")
                             .eq("id", company_id))
        nome = ((azienda or {}).get("ragione_sociale") or "").strip() or None
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("moderazione: email della decisione non preparata (azienda %s, %s)",
                       company_id, getattr(exc, "code", None) or type(exc).__name__)
        return 0
    cta = url_assoluto(percorso or "/app/notifiche")
    inviate = 0
    for destinatario in recapitabili:
        try:
            if await email_service.send_moderazione_decisione_email(
                destinatario["email"], oggetto_tipo=oggetto_tipo, statement=statement,
                cta_url=cta, azienda_destinataria=nome,
            ):
                inviate += 1
        except Exception as exc:  # noqa: BLE001 — destinatario isolato
            logger.warning("moderazione: email della decisione non inviata (utente %s, %s)",
                           destinatario.get("id"), type(exc).__name__)
    return inviate


async def _notifica_autore(
    primary,
    *,
    company_id: Any,
    owner_id: Any,
    tipo: str,
    titolo: str,
    corpo: str,
    url: str | None,
    dedup_key: str,
    statement: str | None = None,
    oggetto_tipo: str | None = None,
) -> None:
    """In-app al titolare e ai membri con visibilità dell'azienda autrice;
    con uno statement of reasons anche l'email al solo titolare (in
    background). Best-effort: la decisione è già avvenuta."""
    if not company_id:
        return
    company = str(company_id)
    try:
        lista = await partenariato_notifiche.destinatari_azienda(primary, company)
        if not lista:
            return
        await notify(primary, [str(d["id"]) for d in lista], tipo=tipo, titolo=titolo,
                     corpo=corpo, url=url, dedup_key=dedup_key, company_profile_id=company)
        if statement is not None and oggetto_tipo is not None:
            titolare = [d for d in lista if str(d["id"]) == str(owner_id)]
            if titolare:
                _spawn(_email_statement(primary, titolare, company, oggetto_tipo, statement,
                                        url))
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("moderazione: notifica %s all'autore non recapitata (azienda %s, %s)",
                       tipo, company, getattr(exc, "code", None) or type(exc).__name__)


async def _notifica_segnalante(primary, riga: Mapping, *, tipo: str, titolo: str,
                               corpo: str, dedup_key: str) -> None:
    """In-app a chi ha segnalato (nessun dato dell'autore). `notify` non
    solleva."""
    segnalante = riga.get("segnalante_user_id")
    if not segnalante:
        return
    await notify(primary, [str(segnalante)], tipo=tipo, titolo=titolo, corpo=corpo,
                 url=_url_segnalazione(riga["id"]), dedup_key=dedup_key)


async def _audit_statement(primary, admin: Mapping, *, owner_id: Any, payload: dict) -> None:
    """Il testo dello statement inviato quando la 0041 non lo salva in una
    segnalazione (decisione d'ufficio, restrizione dal ricorso di chi ha
    segnalato). Best-effort."""
    try:
        await primary.table("audit_log").insert({
            "actor_id": str(admin["id"]),
            "action": AUDIT_STATEMENT,
            "target_user_id": str(owner_id) if owner_id else None,
            "family_parent_id": str(owner_id) if owner_id else None,
            "payload": {**payload, "versione": testi.SOR_VERSIONE},
        }).execute()
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("moderazione: audit dello statement non scritto (%s)",
                       getattr(exc, "code", None) or type(exc).__name__)


def _invalida_indice(oggetto_tipo: str) -> None:
    """Call e profili sospesi (o ripristinati) escono (o rientrano) subito
    dal matching; i messaggi non sono nell'indice."""
    if oggetto_tipo in ("call", "profilo"):
        partenariato_indice.invalida()


# ------------------------------------------------------ utente (segnalazioni)


async def dettaglio(primary, active, user: dict, segnalazione_id: Any) -> SegnalazioneEsitoOut:
    """GET /partenariati/segnalazioni/{id}: chi ha segnalato, o l'azienda
    autrice (azienda attiva) se una restrizione l'ha riguardata; per
    chiunque altro 404."""
    riga = await _carica(primary, segnalazione_id)
    ruolo = _ruolo(riga, active, user)
    if ruolo is None:
        raise NotFoundError(MSG_NON_TROVATA)
    return _esito_out(riga, ruolo, editable=bool(getattr(active, "editable", False)))


async def ricorso(primary, active, user: dict, segnalazione_id: Any, dati: RicorsoIn
                  ) -> SegnalazioneEsitoOut:
    """POST /partenariati/segnalazioni/{id}/ricorso: uno solo, entro 6 mesi
    dalla decisione; il titolare dell'azienda autrice contro una restrizione
    (un membro con visibilità: 403), chi ha segnalato contro
    `nessuna_azione`. Errori: 404, 403, 400 `ricorso_testo_non_valido`, 409
    `ricorso_non_ammesso`."""
    riga = await _carica(primary, segnalazione_id)
    ruolo = _ruolo(riga, active, user)
    if ruolo is None:
        raise NotFoundError(MSG_NON_TROVATA)
    if ruolo == "autore" and not getattr(active, "editable", False):
        raise ForbiddenError(MSG_SOLO_TITOLARE)
    esito = await _rpc(primary, "fn_partner_segnalazione_ricorso", {
        "p_id": str(riga["id"]),
        "p_user": str(user["id"]),
        "p_company": str(active.company_id) if ruolo == "autore" else None,
        "p_testo": dati.testo,
    })
    nuova = esito.get("segnalazione") if isinstance(esito.get("segnalazione"), dict) else None
    if nuova is None:
        raise UpstreamError()
    codice = testi.codice_breve(nuova["id"])
    await notify(
        primary, [str(user["id"])], tipo=testi.TIPO_RICORSO_RICEVUTO,
        titolo=testi.TITOLO_RICORSO_RICEVUTO, corpo=testi.corpo_ricorso_ricevuto(codice),
        url=_url_segnalazione(nuova["id"], active.company_id if ruolo == "autore" else None),
        dedup_key=f"moderazione-ricorso:{nuova['id']}",
        company_profile_id=str(active.company_id) if ruolo == "autore" else None,
    )
    return _esito_out(nuova, ruolo, editable=bool(getattr(active, "editable", False)))


# ------------------------------------------------------------------ admin


async def coda(primary, *, stato: str = "aperte", page: int = 1, page_size: int = 50
               ) -> Page[SegnalazioneAdminOut]:
    """Coda dell'admin: `aperte` (ricevute, in esame e con un ricorso da
    decidere) dalla più vecchia; uno stato o `tutte` dalla più recente."""
    def costruisci():
        query = primary.table("partner_segnalazioni").select(SEGNALAZIONE_SELECT, count="exact")
        if stato == "aperte":
            query = query.in_("stato", list(STATI_APERTI))
        elif stato != "tutte":
            query = query.eq("stato", stato)
        return query.order("created_at", desc=stato != "aperte")

    offset = (page - 1) * page_size
    lette, totale = await pagina(costruisci, offset, page_size)
    righe = [r for r in lette if isinstance(r, dict)]
    denominazioni = await _denominazioni(
        primary, [r.get("autore_company_profile_id") for r in righe])
    return Page.build([_admin_out(r, denominazioni) for r in righe], totale, page,
                      page_size)


async def dettaglio_admin(primary, segnalazione_id: Any) -> SegnalazioneAdminOut:
    return await _admin_una(primary, await _carica(primary, segnalazione_id))


async def prendi_in_carico(primary, admin: dict, segnalazione_id: Any) -> SegnalazioneAdminOut:
    """`ricevuta → in_esame` (già in esame: nessuna scrittura). 409
    `segnalazione_gia_decisa`."""
    esito = await _rpc(primary, "fn_partner_segnalazione_prendi", {
        "p_id": _id_segnalazione(segnalazione_id), "p_admin": str(admin["id"]),
    })
    riga = esito.get("segnalazione")
    if not isinstance(riga, dict):
        raise UpstreamError()
    return await _admin_una(primary, riga)


async def anteprima_statement(primary, segnalazione_id: Any, dati: DecisioneIn) -> StatementOut:
    """Il testo che riceverebbe l'autore con questa decisione (nessuna
    scrittura). La data della decisione è quella di oggi."""
    riga = await _carica(primary, segnalazione_id)
    _coerente(riga, dati.decisione)
    if dati.decisione == "nessuna_azione":
        return StatementOut(versione=testi.SOR_VERSIONE)
    return StatementOut(
        versione=testi.SOR_VERSIONE,
        testo=_statement(riga, motivazione=dati.motivazione, deciso_at=testi.adesso(),
                         origine="segnalazione"),
    )


async def decidi(primary, admin: dict, segnalazione_id: Any, dati: DecisioneIn
                 ) -> SegnalazioneAdminOut:
    """Decisione motivata (RPC con effetto atomico sull'oggetto). Una
    restrizione porta lo statement of reasons all'autore (in-app ed email al
    titolare); chi ha segnalato riceve l'esito. Errori: 404, 400
    `decisione_non_valida`, `motivazione_non_valida`; 409
    `segnalazione_gia_decisa`, `oggetto_non_sospendibile`; 404 per un
    contenuto che non esiste più."""
    riga = await _carica(primary, segnalazione_id)
    _coerente(riga, dati.decisione)
    if riga.get("stato") not in ("ricevuta", "in_esame"):
        raise _errore("segnalazione_gia_decisa")
    restrizione = dati.decisione != "nessuna_azione"
    statement = _statement(riga, motivazione=dati.motivazione, deciso_at=testi.adesso(),
                           origine="segnalazione") if restrizione else None
    esito = await _rpc(primary, "fn_partner_segnalazione_decidi", {
        "p_id": str(riga["id"]),
        "p_admin": str(admin["id"]),
        "p_decisione": dati.decisione,
        "p_motivazione": dati.motivazione,
        "p_sor_testo": statement,
    })
    nuova = esito.get("segnalazione")
    if not isinstance(nuova, dict):
        raise UpstreamError()
    codice = testi.codice_breve(nuova["id"])
    if restrizione:
        if esito.get("effetto") == "applicato":
            _invalida_indice(nuova["oggetto_tipo"])
        company = esito.get("autore_company_id")
        await _notifica_autore(
            primary, company_id=company, owner_id=esito.get("autore_owner_id"),
            tipo=testi.TIPO_DECISIONE, titolo=testi.titolo_restrizione(nuova["oggetto_tipo"]),
            corpo=testi.CORPO_RESTRIZIONE, url=_url_segnalazione(nuova["id"], company),
            dedup_key=f"moderazione-decisione:{nuova['id']}",
            statement=nuova.get("sor_testo") or statement, oggetto_tipo=nuova["oggetto_tipo"],
        )
    await _notifica_segnalante(
        primary, nuova, tipo=testi.TIPO_DECISIONE, titolo=testi.TITOLO_ESITO_SEGNALANTE,
        corpo=testi.corpo_esito_segnalante(dati.decisione, codice),
        dedup_key=f"moderazione-esito:{nuova['id']}",
    )
    return await _admin_una(primary, nuova, effetto=esito.get("effetto"))


async def decidi_ricorso(primary, admin: dict, segnalazione_id: Any, dati: RicorsoDecisioneIn
                         ) -> SegnalazioneAdminOut:
    """Decisione motivata del ricorso. `riformata` annulla la restrizione
    (salvo un'altra decisione valida sullo stesso contenuto: effetto
    `mantenuto`) o, se la decisione era `nessuna_azione`, la applica ora:
    allora l'autore riceve lo statement of reasons. Chi ha presentato il
    ricorso riceve l'esito. Errori: 404, 400, 409 `ricorso_non_in_attesa`."""
    riga = await _carica(primary, segnalazione_id)
    if riga.get("stato") != "ricorso_presentato":
        raise _errore("ricorso_non_in_attesa")
    esito = await _rpc(primary, "fn_partner_ricorso_decidi", {
        "p_id": str(riga["id"]),
        "p_admin": str(admin["id"]),
        "p_esito": dati.esito,
        "p_motivazione": dati.motivazione,
    })
    nuova = esito.get("segnalazione")
    if not isinstance(nuova, dict):
        raise UpstreamError()
    effetto = esito.get("effetto")
    oggetto = nuova["oggetto_tipo"]
    company = esito.get("autore_company_id") or nuova.get("autore_company_profile_id")
    owner = esito.get("autore_owner_id")
    codice = testi.codice_breve(nuova["id"])
    if effetto in ("applicato", "annullato"):
        _invalida_indice(oggetto)
    titolo = testi.titolo_ricorso_deciso(dati.esito)
    corpo = testi.corpo_ricorso_deciso(codice, mantenuto=effetto == "mantenuto")
    dedup = f"moderazione-ricorso-deciso:{nuova['id']}"
    if _ricorso_da(nuova) == "segnalante":
        await _notifica_segnalante(primary, nuova, tipo=testi.TIPO_RICORSO_DECISO, titolo=titolo,
                                   corpo=corpo, dedup_key=dedup)
        if effetto == "applicato":
            deciso_at = _istante(nuova.get("ricorso_deciso_at")) or testi.adesso()
            statement = _statement(nuova, motivazione=dati.motivazione, deciso_at=deciso_at,
                                   origine="ricorso")
            await _audit_statement(primary, admin, owner_id=owner, payload={
                "segnalazione_id": str(nuova["id"]), "oggetto_tipo": oggetto,
                "origine": "ricorso", "testo": statement})
            await _notifica_autore(
                primary, company_id=company, owner_id=owner, tipo=testi.TIPO_DECISIONE,
                titolo=testi.titolo_restrizione(oggetto), corpo=testi.CORPO_RESTRIZIONE,
                url=_url_segnalazione(nuova["id"], company),
                dedup_key=f"moderazione-decisione:{nuova['id']}", statement=statement,
                oggetto_tipo=oggetto,
            )
    else:
        await _notifica_autore(
            primary, company_id=company, owner_id=owner, tipo=testi.TIPO_RICORSO_DECISO,
            titolo=titolo, corpo=corpo, url=_url_segnalazione(nuova["id"], company),
            dedup_key=dedup,
        )
    return await _admin_una(primary, nuova, effetto=effetto)


async def sospendi(primary, admin: dict, oggetto_tipo: str, oggetto_id: Any, dati: SospendiIn
                   ) -> SospensioneOut:
    """Sospensione diretta dell'admin, senza segnalazione (call da bozza o
    pubblicata, profilo, messaggio), motivazione 20..500. Se cambia qualcosa
    l'autore riceve lo statement of reasons «d'ufficio»: in-app nella forma
    breve (`statement_breve`, con la motivazione per intero: non c'è una
    pagina della segnalazione da aprire), per email il testo completo, che
    resta nell'audit. Errori: 404, 400, 409 `oggetto_non_sospendibile`."""
    riferimento = id_oggetto(oggetto_tipo, oggetto_id)
    esito = await _rpc(primary, "fn_partner_admin_sospendi", {
        "p_oggetto_tipo": oggetto_tipo,
        "p_oggetto_id": riferimento,
        "p_admin": str(admin["id"]),
        "p_motivazione": dati.motivazione,
    })
    if esito.get("modificato"):
        _invalida_indice(oggetto_tipo)
        company = esito.get("autore_company_id")
        owner = esito.get("autore_owner_id")
        deciso_at = testi.adesso()
        statement = testi.genera_statement(oggetto_tipo=oggetto_tipo,
                                           motivazione=dati.motivazione,
                                           deciso_at=deciso_at, origine="ufficio")
        await _audit_statement(primary, admin, owner_id=owner, payload={
            "oggetto_tipo": oggetto_tipo, "oggetto_id": riferimento, "origine": "ufficio",
            "testo": statement})
        if company:
            url = await _url_oggetto(primary, oggetto_tipo, riferimento, str(company))
            await _notifica_autore(
                primary, company_id=company, owner_id=owner, tipo=testi.TIPO_DECISIONE,
                titolo=testi.titolo_restrizione(oggetto_tipo),
                corpo=testi.statement_breve(oggetto_tipo=oggetto_tipo,
                                            motivazione=dati.motivazione, deciso_at=deciso_at),
                url=url, dedup_key=f"moderazione-ufficio:{oggetto_tipo}:{riferimento}:"
                f"{uuid.uuid4()}", statement=statement, oggetto_tipo=oggetto_tipo,
            )
    return SospensioneOut(oggetto_tipo=oggetto_tipo, oggetto_id=riferimento,
                          esito=str(esito.get("esito")), stato=esito.get("stato"),
                          modificato=bool(esito.get("modificato")))


async def ripristina(primary, admin: dict, oggetto_tipo: str, oggetto_id: Any,
                     dati: SospensioneIn) -> SospensioneOut:
    """Ripristino diretto dell'admin: call allo stato precedente (o scaduta
    se nel frattempo è passata la scadenza), profilo riattivato, messaggio
    di nuovo visibile; avviso in-app all'autore. Errori: 404, 400."""
    riferimento = id_oggetto(oggetto_tipo, oggetto_id)
    esito = await _rpc(primary, "fn_partner_admin_ripristina", {
        "p_oggetto_tipo": oggetto_tipo,
        "p_oggetto_id": riferimento,
        "p_admin": str(admin["id"]),
        "p_motivazione": dati.motivazione,
    })
    if esito.get("modificato"):
        _invalida_indice(oggetto_tipo)
        company = esito.get("autore_company_id")
        if company:
            url = await _url_oggetto(primary, oggetto_tipo, riferimento, str(company))
            await _notifica_autore(
                primary, company_id=company, owner_id=esito.get("autore_owner_id"),
                tipo=testi.TIPO_RIPRISTINO, titolo=testi.titolo_ripristino(oggetto_tipo),
                corpo=testi.CORPO_RIPRISTINO, url=url,
                dedup_key=f"moderazione-ripristino:{oggetto_tipo}:{riferimento}:"
                f"{uuid.uuid4()}",
            )
    return SospensioneOut(oggetto_tipo=oggetto_tipo, oggetto_id=riferimento,
                          esito=str(esito.get("esito")), stato=esito.get("stato"),
                          modificato=bool(esito.get("modificato")))


# ---------------------------------------------------------------- contesto


async def _audit_obbligatorio(primary, admin: Mapping, azione: str, payload: dict) -> None:
    """Audit dell'accesso ai messaggi privati, PRIMA di leggerli: se non si
    scrive, 502 e nessun dato (fail-closed)."""
    try:
        await primary.table("audit_log").insert({
            "actor_id": str(admin["id"]),
            "action": azione,
            "target_user_id": None,
            "family_parent_id": None,
            "payload": payload,
        }).execute()
    except Exception as exc:  # noqa: BLE001 — fail-closed
        logger.error("moderazione: audit %s non scritto, contesto negato (%s)", azione,
                     getattr(exc, "code", None) or type(exc).__name__)
        raise UpstreamError() from exc


def _messaggio_out(riga: Mapping, autore: Any, segnalato: int) -> MessaggioContestoOut:
    return MessaggioContestoOut(
        id=int(riga["id"]),
        lato="autore" if str(riga.get("mittente_company_profile_id")) == str(autore)
        else "altra",
        testo=riga.get("testo"),
        oscurato=riga.get("nascosto_moderazione_at") is not None,
        segnalato=int(riga["id"]) == segnalato,
        created_at=riga.get("created_at"),
    )


async def _tutti(primary, conversazione_id: str) -> tuple[list[dict], bool]:
    """La conversazione intera a pagine (keyset sull'id), fino al tetto."""
    righe: list[dict] = []
    ultimo: int | None = None
    while len(righe) < CONTESTO_COMPLETO_MAX:
        query = (primary.table("partner_messaggi").select(MESSAGGIO_SELECT)
                 .eq("conversazione_id", conversazione_id))
        if ultimo is not None:
            query = query.gt("id", ultimo)
        resp = await query.order("id").limit(_PAGINA).execute()
        pagina = [r for r in resp.data or [] if isinstance(r, dict)]
        righe.extend(pagina)
        if len(pagina) < _PAGINA:
            return righe[:CONTESTO_COMPLETO_MAX], False
        ultimo = int(pagina[-1]["id"])
    return righe[:CONTESTO_COMPLETO_MAX], True


async def contesto(primary, admin: dict, segnalazione_id: Any, *, completo: bool = False,
                   motivazione: str | None = None) -> ContestoOut:
    """Contesto di un messaggio segnalato: ±10 messaggi attorno a quello
    segnalato; la conversazione intera solo con una motivazione (20..2000)
    registrata nell'audit. L'audit si scrive prima di leggere i messaggi
    (fail-closed: senza audit 502 e nessun dato)."""
    riga = await _carica(primary, segnalazione_id)
    if riga.get("oggetto_tipo") != "messaggio":
        raise AppError(400, "contesto_non_disponibile", MSG_CONTESTO_SOLO_MESSAGGI)
    motivo = testo_limitato(motivazione, "motivazione_non_valida", MSG_MOTIVAZIONE) \
        if completo else None
    try:
        messaggio_id = int(str(riga["oggetto_id"]))
    except ValueError:
        raise NotFoundError(MSG_MESSAGGIO_SPARITO) from None
    messaggio = await _una(primary.table("partner_messaggi")
                           .select("id,conversazione_id,mittente_company_profile_id")
                           .eq("id", messaggio_id))
    conv = await _una(
        primary.table("partner_conversazioni").select(CONVERSAZIONE_SELECT)
        .eq("id", str(messaggio["conversazione_id"]))
    ) if messaggio else None
    if messaggio is None or conv is None:
        raise NotFoundError(MSG_MESSAGGIO_SPARITO)
    autore = messaggio.get("mittente_company_profile_id")
    payload = {"segnalazione_id": str(riga["id"]), "conversazione_id": str(conv["id"]),
               "messaggio_id": messaggio_id}
    if completo:
        await _audit_obbligatorio(primary, admin, AUDIT_CONTESTO_COMPLETO,
                                  {**payload, "motivazione": motivo})
        righe, troncato = await _tutti(primary, str(conv["id"]))
        return ContestoOut(completo=True, troncato=troncato,
                           messaggi=[_messaggio_out(r, autore, messaggio_id) for r in righe])
    await _audit_obbligatorio(primary, admin, AUDIT_CONTESTO, payload)

    def base():
        return (primary.table("partner_messaggi").select(MESSAGGIO_SELECT)
                .eq("conversazione_id", str(conv["id"])))

    prima_resp, centro, dopo_resp = await asyncio.gather(
        base().lt("id", messaggio_id).order("id", desc=True)
        .limit(CONTESTO_FINESTRA + 1).execute(),
        base().eq("id", messaggio_id).limit(1).execute(),
        base().gt("id", messaggio_id).order("id").limit(CONTESTO_FINESTRA + 1).execute(),
    )
    prima = [r for r in prima_resp.data or [] if isinstance(r, dict)]
    dopo = [r for r in dopo_resp.data or [] if isinstance(r, dict)]
    righe = [*reversed(prima[:CONTESTO_FINESTRA]), *(centro.data or []),
             *dopo[:CONTESTO_FINESTRA]]
    return ContestoOut(
        completo=False,
        messaggi=[_messaggio_out(r, autore, messaggio_id) for r in righe],
        altri_prima=len(prima) > CONTESTO_FINESTRA,
        altri_dopo=len(dopo) > CONTESTO_FINESTRA,
    )
