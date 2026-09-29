"""Chat in-app tra il creatore di una call e il partner accettato (WP7,
docs/partenariati.md K3-K4, Q13, Q14, Q21).

- Una conversazione per candidatura accettata (la crea `fn_partner_decidi`
  nella transazione dell'accettazione). La leggono SOLO le due aziende
  (titolare e membri con visibilità); scrive e chiude solo il titolare (Q14),
  e chiude solo il creatore della call (K4). Sola lettura se chiusa o se una
  delle due aziende non è più attiva (Q21).
- Messaggi 1..5000 caratteri, immutabili (trigger: si oscurano solo per
  moderazione e allora escono come `{testo: null, nascosto: true}`),
  idempotenti per `client_msg_id`. NESSUN filtro anti-contatti: i contatti si
  scambiano qui (i due banner fissi, antitrust e identità non verificata, li
  mostra il frontend). Mai id di utenti o di aziende verso l'altra parte:
  un messaggio è solo «tuo» o «dell'altra azienda».
- Letture per utente (`fn_partner_segna_letto`, anche i membri: è il loro
  stato di lettura) e non letti nel riepilogo.
- «Una email per raffica»: dopo un messaggio nuovo il claim condizionato
  `fn_partner_claim_email_chat` sceglie gli utenti dell'altra azienda che non
  hanno letto fin lì e non hanno già un avviso in sospeso; solo a loro la
  notifica in-app e, se hanno `eventi_abilitati` e sono recapitabili, l'email
  (MAI il testo dei messaggi). In background: l'invio non aspetta.
- Anti-abuso (fail-open): messaggi all'ora per utente.
- Identità della controparte solo con la rivelazione accesa e il suo audit
  (`partenariato_candidature_service.identita_se_rivelata`).

Log: solo id e codici; mai testi, email o nomi.
"""

import asyncio
import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal, NoReturn
from uuid import UUID

from postgrest.exceptions import APIError
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import get_settings
from app.core.errors import AppError, ForbiddenError, NotFoundError, UpstreamError
from app.schemas.common import Page
from app.services import email_service, partenariato_indice, partenariato_notifiche
from app.services import partenariato_candidature_service as candidature
from app.services.partenariato_accesso import IdentitaRivelataOut
from app.services.partenariato_candidature_service import (
    CALL_RIF_SELECT,
    CallRiferimentoOut,
    call_riferimento,
    notifica_evento,
    pulisci_testo,
    url_assoluto,
)
from app.services.partenariato_errori import raise_from_rpc

logger = logging.getLogger("bandofit.partenariati")

TESTO_MAX = 5000
MESSAGGI_LIMITE_PAGINA = 100
FINESTRA_ORA_SECONDI = 3600
TIPO_NUOVI_MESSAGGI = "partenariato.nuovi_messaggi"

MSG_SOLO_TITOLARE = "In chat scrive il titolare dell'azienda"
MSG_NON_TROVATA = "Conversazione non trovata"
MSG_LIMITE_MESSAGGI = "Stai scrivendo molti messaggi: riprova tra qualche minuto"

CONVERSAZIONE_SELECT = (
    "id,partner_call_id,candidatura_id,company_creatore_id,company_partner_id,stato,chiusa_at,"
    "ultimo_messaggio_id,ultimo_messaggio_at,created_at"
)
MESSAGGIO_SELECT = (
    "id,conversazione_id,mittente_company_profile_id,testo,client_msg_id,"
    "nascosto_moderazione_at,created_at"
)


# ------------------------------------------------------------------ input


class MessaggioIn(BaseModel):
    """POST /partenariati/conversazioni/{id}/messaggi. `client_msg_id` lo
    genera il client: un secondo invio con la stessa chiave restituisce il
    messaggio già scritto."""

    model_config = ConfigDict(extra="forbid")

    testo: str
    client_msg_id: UUID

    @field_validator("testo", mode="before")
    @classmethod
    def _testo(cls, valore: Any) -> str:
        return pulisci_testo(valore, "Il messaggio", minimo=1, massimo=TESTO_MAX)


class LettoIn(BaseModel):
    """POST /partenariati/conversazioni/{id}/letto: fino a quale messaggio
    (assente = fino all'ultimo)."""

    model_config = ConfigDict(extra="forbid")

    fino_a_id: int | None = Field(default=None, ge=0)


# ------------------------------------------------------------------ output


class _Uscita(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ControparteOut(_Uscita):
    """L'altra azienda della conversazione: per il creatore lo pseudonimo
    della candidata per quella call; per il partner nessun handle (è «il
    creatore della call»). `attiva` false = eliminata o archiviata (la
    conversazione resta in sola lettura)."""

    pseudonimo: str | None = None
    attiva: bool = True


class ConversazioneRigaOut(_Uscita):
    """Una conversazione nella lista (`lato`: `creatore` o `partner`)."""

    id: UUID
    stato: Literal["aperta", "chiusa"]
    lato: Literal["creatore", "partner"]
    call: CallRiferimentoOut
    controparte: ControparteOut
    non_letti: int = 0
    ultimo_messaggio_at: datetime | None = None
    created_at: datetime | None = None
    chiusa_at: datetime | None = None
    sola_lettura: bool = False


class ConversazioneOut(ConversazioneRigaOut):
    """GET /partenariati/conversazioni/{id}: più l'identità della controparte
    (solo con la rivelazione accesa), cosa può fare l'utente e fin dove ha
    letto."""

    candidatura_id: UUID
    identita_rivelata: bool = False
    identita: IdentitaRivelataOut | None = None
    letto_fino_a_id: int = 0
    puo_scrivere: bool = False
    puo_chiudere: bool = False


class MessaggioOut(_Uscita):
    """Un messaggio: `propria` = scritto dalla propria azienda (mai chi, mai
    id di utenti); oscurato → `testo` null e `nascosto` true.
    `client_msg_id` solo per i propri (riconciliazione dell'invio)."""

    id: int
    propria: bool
    testo: str | None = None
    nascosto: bool = False
    created_at: datetime | None = None
    client_msg_id: UUID | None = None


class MessaggiOut(_Uscita):
    items: list[MessaggioOut]
    ha_altri: bool = False


# ------------------------------------------------------------------ utilità

# Riferimenti ai task in background (avvisi dei nuovi messaggi).
_background_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    """Avvisi dei nuovi messaggi in background (sostituibile nei test)."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


# Il detail comune con il profilo partner ha lì un messaggio sul profilo.
_ERRORI_CHAT: dict[str, tuple[int, str, str]] = {
    "attore_non_titolare": (403, "forbidden", MSG_SOLO_TITOLARE),
}


def _errore_rpc(exc: APIError) -> NoReturn:
    detail = (exc.details or "").strip()
    if detail in _ERRORI_CHAT:
        raise AppError(*_ERRORI_CHAT[detail]) from exc
    raise_from_rpc(exc)


async def _rpc(primary, nome: str, parametri: dict) -> Any:
    try:
        resp = await primary.rpc(nome, parametri).execute()
    except APIError as exc:
        _errore_rpc(exc)
    return resp.data


async def _una(query) -> dict | None:
    resp = await query.limit(1).execute()
    return resp.data[0] if resp.data and isinstance(resp.data[0], dict) else None


def _richiedi_azienda(active) -> str:
    if not active.company_id:
        raise NotFoundError(MSG_NON_TROVATA)
    return str(active.company_id)


def _normalizza(valore: Any) -> str:
    try:
        return str(UUID(str(valore).strip()))
    except (ValueError, AttributeError, TypeError):
        raise NotFoundError(MSG_NON_TROVATA) from None


def _lato(conv: Mapping, company_id: str) -> Literal["creatore", "partner"] | None:
    if str(conv.get("company_creatore_id")) == company_id:
        return "creatore"
    if str(conv.get("company_partner_id")) == company_id:
        return "partner"
    return None


def _altra(conv: Mapping, company_id: str) -> str:
    return str(conv["company_partner_id"] if _lato(conv, company_id) == "creatore"
               else conv["company_creatore_id"])


async def _conversazione(primary, conversazione_id: Any, company_id: str) -> dict:
    """La conversazione se l'azienda attiva ne è parte (404 altrimenti, anche
    per un'altra azienda dello stesso owner)."""
    resp = (
        await primary.table("partner_conversazioni").select(CONVERSAZIONE_SELECT)
        .eq("id", _normalizza(conversazione_id)).limit(1).execute()
    )
    conv = resp.data[0] if resp.data else None
    if not isinstance(conv, dict) or _lato(conv, company_id) is None:
        raise NotFoundError(MSG_NON_TROVATA)
    return conv


async def _carica_contesto(primary, convs: list[dict], company_id: str):
    """Call, pseudonimi (dalle candidature), aziende vive e, dal lato
    partner, identificativi dei creatori (per il titolo della call), a
    blocco."""
    call_ids = sorted({str(c["partner_call_id"]) for c in convs})
    cand_ids = sorted({str(c["candidatura_id"]) for c in convs})
    calls: dict[str, dict] = {}
    pseudonimi: dict[str, str] = {}
    for inizio in range(0, len(call_ids), 100):
        resp = await primary.table("partner_calls").select(CALL_RIF_SELECT) \
            .in_("id", call_ids[inizio : inizio + 100]).execute()
        calls.update({str(r["id"]): r for r in resp.data or []})
    for inizio in range(0, len(cand_ids), 100):
        resp = await primary.table("partner_candidature").select("id,pseudonimo") \
            .in_("id", cand_ids[inizio : inizio + 100]).execute()
        pseudonimi.update({str(r["id"]): r.get("pseudonimo") for r in resp.data or []})
    vive = await partenariato_indice.aziende_vive(
        primary, sorted({_altra(c, company_id) for c in convs} | {company_id})
    )
    ident = await candidature.ident_creatori(
        primary, [c["company_creatore_id"] for c in convs if _lato(c, company_id) == "partner"]
    )
    return calls, pseudonimi, vive, ident


def _riga_out(conv: Mapping, company_id: str, calls, pseudonimi, vive, ident, *,
              non_letti: int, editable: bool) -> ConversazioneRigaOut:
    lato = _lato(conv, company_id)
    attiva = _altra(conv, company_id) in vive
    call = calls.get(str(conv["partner_call_id"])) or {"id": conv["partner_call_id"]}
    return ConversazioneRigaOut(
        id=conv["id"],
        stato=conv["stato"],
        lato=lato,
        call=call_riferimento(call, ident.get(str(conv["company_creatore_id"]))
                              if lato == "partner" else None),
        controparte=ControparteOut(
            pseudonimo=pseudonimi.get(str(conv["candidatura_id"])) if lato == "creatore"
            else None,
            attiva=attiva,
        ),
        non_letti=max(0, int(non_letti or 0)),
        ultimo_messaggio_at=conv.get("ultimo_messaggio_at"),
        created_at=conv.get("created_at"),
        chiusa_at=conv.get("chiusa_at"),
        sola_lettura=_sola_lettura(conv, attiva, company_id in vive, editable),
    )


def _sola_lettura(conv: Mapping, controparte_attiva: bool, propria_attiva: bool,
                  editable: bool) -> bool:
    return (conv.get("stato") != "aperta" or not controparte_attiva or not propria_attiva
            or not editable)


async def _riepilogo(primary, user_id: str, company_id: str) -> list[dict]:
    resp = await primary.rpc("fn_partner_conversazioni_riepilogo", {
        "p_user": user_id, "p_company": company_id,
    }).execute()
    return [r for r in resp.data or [] if isinstance(r, dict)]


# ------------------------------------------------------------------ letture


async def lista_conversazioni(primary, secondary, active, user: dict, *, page: int = 1,
                              page_size: int = 20) -> Page[ConversazioneRigaOut]:
    """Le conversazioni dell'azienda attiva (titolare e membri con
    visibilità), dall'ultimo messaggio, con i non letti dell'utente."""
    if not active.company_id:
        return Page.build([], 0, page, page_size)
    company_id = str(active.company_id)
    righe = await _riepilogo(primary, str(user["id"]), company_id)
    offset = (page - 1) * page_size
    pagina = righe[offset : offset + page_size]
    if not pagina:
        return Page.build([], len(righe), page, page_size)
    ids = [str(r["conversazione_id"]) for r in pagina]
    resp = await primary.table("partner_conversazioni").select(CONVERSAZIONE_SELECT) \
        .in_("id", ids).execute()
    per_id = {str(c["id"]): c for c in resp.data or [] if _lato(c, company_id)}
    convs = [per_id[i] for i in ids if i in per_id]
    calls, pseudonimi, vive, ident = await _carica_contesto(primary, convs, company_id)
    non_letti = {str(r["conversazione_id"]): r.get("non_letti") or 0 for r in pagina}
    items = [
        _riga_out(c, company_id, calls, pseudonimi, vive, ident,
                  non_letti=non_letti[str(c["id"])], editable=bool(active.editable))
        for c in convs
    ]
    return Page.build(items, len(righe), page, page_size)


async def dettaglio(primary, secondary, active, user: dict, conversazione_id: Any
                    ) -> ConversazioneOut:
    company_id = _richiedi_azienda(active)
    conv = await _conversazione(primary, conversazione_id, company_id)
    return await _dettaglio_out(primary, active, user, conv)


async def _dettaglio_out(primary, active, user: dict, conv: dict) -> ConversazioneOut:
    company_id = str(active.company_id)
    calls, pseudonimi, vive, ident = await _carica_contesto(primary, [conv], company_id)
    riepilogo = {str(r["conversazione_id"]): r for r in
                 await _riepilogo(primary, str(user["id"]), company_id)}
    lettura = await (
        primary.table("partner_conversazione_letture").select("letto_fino_a_id")
        .eq("conversazione_id", str(conv["id"])).eq("user_id", str(user["id"])).limit(1)
        .execute()
    )
    base = _riga_out(conv, company_id, calls, pseudonimi, vive, ident,
                     non_letti=(riepilogo.get(str(conv["id"])) or {}).get("non_letti") or 0,
                     editable=bool(active.editable))
    identita = await candidature.identita_se_rivelata(primary, conv["candidatura_id"],
                                                      _altra(conv, company_id))
    return ConversazioneOut(
        **base.model_dump(),
        candidatura_id=conv["candidatura_id"],
        identita_rivelata=identita is not None,
        identita=identita,
        letto_fino_a_id=int((lettura.data[0] if lettura.data else {}).get("letto_fino_a_id")
                            or 0),
        puo_scrivere=not base.sola_lettura,
        puo_chiudere=bool(active.editable and base.lato == "creatore"
                          and conv.get("stato") == "aperta"),
    )


def _messaggio_out(riga: Mapping, company_id: str) -> MessaggioOut:
    propria = str(riga.get("mittente_company_profile_id")) == company_id
    nascosto = riga.get("nascosto_moderazione_at") is not None
    return MessaggioOut(
        id=int(riga["id"]),
        propria=propria,
        testo=None if nascosto else riga.get("testo"),
        nascosto=nascosto,
        created_at=riga.get("created_at"),
        client_msg_id=riga.get("client_msg_id") if propria else None,
    )


async def messaggi(primary, secondary, active, user: dict, conversazione_id: Any, *,
                   dopo: int | None = None, prima: int | None = None, limite: int = 50
                   ) -> MessaggiOut:
    """Messaggi in ordine crescente: `dopo` (polling: i nuovi oltre quell'id),
    `prima` (scorrimento all'indietro) o, senza cursori, gli ultimi.
    `ha_altri`: ce ne sono altri nella stessa direzione."""
    company_id = _richiedi_azienda(active)
    conv = await _conversazione(primary, conversazione_id, company_id)
    limite = max(1, min(int(limite), MESSAGGI_LIMITE_PAGINA))
    query = primary.table("partner_messaggi").select(MESSAGGIO_SELECT) \
        .eq("conversazione_id", str(conv["id"]))
    if dopo is not None:
        query = query.gt("id", int(dopo)).order("id")
    else:
        if prima is not None:
            query = query.lt("id", int(prima))
        query = query.order("id", desc=True)
    resp = await query.limit(limite + 1).execute()
    righe = [r for r in resp.data or [] if isinstance(r, dict)]
    ha_altri = len(righe) > limite
    righe = righe[:limite]
    if dopo is None:
        righe.reverse()
    return MessaggiOut(items=[_messaggio_out(r, company_id) for r in righe], ha_altri=ha_altri)


async def non_letti_totali(primary, active, user: dict) -> int:
    """Messaggi non letti dall'utente nelle conversazioni dell'azienda attiva
    (badge del menu). Best-effort: un errore vale 0."""
    if not active.company_id:
        return 0
    try:
        righe = await _riepilogo(primary, str(user["id"]), str(active.company_id))
    except Exception as exc:  # noqa: BLE001 — il badge non rompe il menu
        logger.warning("chat: non letti del riepilogo non letti (%s)",
                       getattr(exc, "code", None) or type(exc).__name__)
        return 0
    return sum(max(0, int(r.get("non_letti") or 0)) for r in righe)


# ------------------------------------------------------------------ scritture


async def invia(primary, secondary, active, user: dict, conversazione_id: Any, dati: MessaggioIn
                ) -> MessaggioOut:
    """Messaggio del titolare di una delle due aziende (i membri leggono).
    Errori: 403, 404, 409 (`conversazione_chiusa`,
    `controparte_non_disponibile`), 429 `limite_messaggi`."""
    if not active.editable:
        raise ForbiddenError(MSG_SOLO_TITOLARE)
    company_id = _richiedi_azienda(active)
    conv = await _conversazione(primary, conversazione_id, company_id)
    await candidature.limite_anti_abuso(
        primary, "partner_messaggio", user, get_settings().partner_messaggi_limite_ora,
        FINESTRA_ORA_SECONDI, "limite_messaggi", MSG_LIMITE_MESSAGGI,
    )
    esito = await _rpc(primary, "fn_partner_invia_messaggio", {
        "p_conversazione": str(conv["id"]),
        "p_attore": str(user["id"]),
        "p_owner": str(active.owner_id),
        "p_company": company_id,
        "p_testo": dati.testo,
        "p_client_msg_id": str(dati.client_msg_id),
    })
    riga = (esito or {}).get("messaggio") if isinstance(esito, dict) else None
    if not isinstance(riga, dict) or riga.get("id") is None:
        raise UpstreamError()
    if not esito.get("duplicato"):
        _spawn(dopo_messaggio(
            primary, conv, str(esito.get("company_destinataria_id") or _altra(conv, company_id)),
            int(riga["id"]),
        ))
    return _messaggio_out(riga, company_id)


async def dopo_messaggio(primary, conv: Mapping, destinataria: str, messaggio_id: int) -> int:
    """Una notifica (in-app ed email) per raffica agli utenti dell'azienda
    destinataria: il claim condizionato sceglie chi non ha letto fin lì e non
    ha un avviso in sospeso. MAI il testo. Non solleva mai. → avvisati."""
    try:
        destinatari = await partenariato_notifiche.destinatari_azienda(primary, destinataria)
        if not destinatari:
            return 0
        claim = await primary.rpc("fn_partner_claim_email_chat", {
            "p_conversazione": str(conv["id"]),
            "p_company": destinataria,
            "p_user_ids": [str(d["id"]) for d in destinatari],
            "p_ultimo_id": messaggio_id,
        }).execute()
        rivendicati = {str(u) for u in claim.data or []}
        scelti = [d for d in destinatari if str(d["id"]) in rivendicati]
        if not scelti:
            return 0
        call = await _una(
            primary.table("partner_calls").select(CALL_RIF_SELECT)
            .eq("id", str(conv["partner_call_id"]))
        ) or {}
        pseudo = None
        if destinataria == str(conv["company_creatore_id"]):
            # Al creatore l'altra azienda si presenta con lo pseudonimo della call.
            riga = await _una(
                primary.table("partner_candidature").select("id,pseudonimo")
                .eq("id", str(conv["candidatura_id"]))
            )
            pseudo = (riga or {}).get("pseudonimo")
        percorso = f"/app/partenariati/conversazioni/{conv['id']}?azienda={destinataria}"
        bando = call.get("bando_titolo")
        await notifica_evento(
            primary,
            company_id=destinataria,
            tipo=TIPO_NUOVI_MESSAGGI,
            titolo="Hai nuovi messaggi",
            corpo=f"Ci sono nuovi messaggi nella conversazione sulla call per il bando "
            f"«{bando or 'del catalogo'}».",
            url=percorso,
            dedup_key=f"partner-chat:{conv['id']}:{messaggio_id}",
            destinatari=scelti,
            email=lambda to, token, azienda: email_service.send_partner_messaggi_email(
                to, bando_titolo=bando, cta_url=url_assoluto(percorso),
                unsubscribe_token=token, pseudonimo=pseudo, azienda_destinataria=azienda),
        )
        return len(scelti)
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("chat: avviso dei nuovi messaggi non inviato (conversazione %s, %s)",
                       conv.get("id"), getattr(exc, "code", None) or type(exc).__name__)
        return 0


async def segna_letto(primary, secondary, active, user: dict, conversazione_id: Any,
                      dati: LettoIn) -> int:
    """Lettura dell'utente (anche un membro: è la sua). → `letto_fino_a_id`."""
    company_id = _richiedi_azienda(active)
    conv = await _conversazione(primary, conversazione_id, company_id)
    esito = await _rpc(primary, "fn_partner_segna_letto", {
        "p_conversazione": str(conv["id"]),
        "p_user": str(user["id"]),
        "p_company": company_id,
        "p_fino_a": dati.fino_a_id,
    })
    return int(esito or 0)


async def chiudi(primary, secondary, active, user: dict, conversazione_id: Any
                 ) -> ConversazioneOut:
    """Chiusura (K4): solo il titolare dell'azienda creatrice; lo storico
    resta in sola lettura. Errori: 403, 404 (anche per il partner), 409
    `conversazione_chiusa`."""
    if not active.editable:
        raise ForbiddenError("La conversazione la chiude il titolare dell'azienda")
    company_id = _richiedi_azienda(active)
    conv = await _conversazione(primary, conversazione_id, company_id)
    if _lato(conv, company_id) != "creatore":
        raise NotFoundError(MSG_NON_TROVATA)
    await _rpc(primary, "fn_partner_chiudi_conversazione", {
        "p_conversazione": str(conv["id"]),
        "p_attore": str(user["id"]),
        "p_owner": str(active.owner_id),
        "p_company": company_id,
    })
    return await _dettaglio_out(
        primary, active, user, await _conversazione(primary, conv["id"], company_id)
    )

