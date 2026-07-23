"""Consulenze (DB primario, service_role): slot di disponibilità del
progettista e flusso richiesta → proposta → assegnazione → prenotazione.

La concorrenza vive a livello DB (migration 0017): qui si validano gli input,
si chiamano le RPC e si traducono i loro detail-code in errori HTTP, con la
stessa meccanica di family_service.raise_from_rpc.

Eventi (4, dal requisito): la notifica in-app è il canale AFFIDABILE e viene
scritta per prima (dedup a DB); l'email è best-effort e parte in background
(pattern _spawn di ai_check_service). Le transizioni sono irripetibili per
costruzione (vincoli DB + RPC one-way): ogni evento scatta al più una volta.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from functools import partial
from typing import NoReturn
from zoneinfo import ZoneInfo

from decimal import Decimal

from postgrest.exceptions import APIError

from app.core.config import get_settings
from app.core.errors import (
    AppError,
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    PaymentRequiredError,
    UpstreamError,
)
from app.schemas.consulting import (
    AppuntamentoOut,
    BookingOut,
    ConsulenzaOut,
    FullCompanyOut,
    ProgettistaPublicOut,
    ProposalOut,
    RichiestaPoolDetailOut,
    RichiestaPoolOut,
    RichiestePoolResponse,
    SerieCreateOut,
    SerieDeleteOut,
    SerieIn,
    SlotIn,
    SlotOut,
)
from app.services import (
    ai_check_service,
    company_service,
    email_service,
    family_service,
    notification_service,
    openapi_service,
)

logger = logging.getLogger("bandofit.consulting")

# Sanity check sulla durata: la durata è libera (decisione di dominio), questi
# limiti intercettano solo refusi (slot di 2 minuti o di 3 giorni).
MIN_SLOT = timedelta(minutes=15)
MAX_SLOT = timedelta(hours=12)

SLOT_SELECT = "id,inizio,fine,serie_id"
REQUEST_SELECT = (
    "id,cliente_id,family_parent_id,company_profile_id,ai_check_id,esito,punteggio,"
    "bando_id,bando_slug,bando_titolo,stato,assigned_progettista_id,assigned_at,"
    "accepted_proposal_id,created_at"
)
PROPOSAL_SELECT = "id,request_id,progettista_id,messaggio,stato,created_at"
BOOKING_SELECT = "id,request_id,slot_id,cliente_id,progettista_id,inizio,fine,stato,videocall_token"

# Codici PostgreSQL: violazione di UNIQUE e di EXCLUDE constraint.
_UNIQUE_VIOLATION = "23505"
_EXCLUSION_VIOLATION = "23P01"

# codice detail delle RPC 0017 -> (classe errore, messaggio per l'utente)
_RPC_ERRORS: dict[str, tuple[type[AppError], str]] = {
    "request_not_found": (NotFoundError, "Consulenza non trovata"),
    "not_request_owner": (
        ForbiddenError,
        "Solo il titolare dell'azienda può gestire questa consulenza",
    ),
    "request_not_assigned": (ConflictError, "La consulenza non è ancora assegnata"),
    "request_not_open": (ConflictError, "La richiesta non è più aperta"),
    "proposal_not_found": (NotFoundError, "Proposta non trovata"),
    "proposal_not_open": (ConflictError, "La proposta non è più disponibile"),
    "progettista_not_available": (ConflictError, "Il progettista non è più disponibile"),
    "slot_not_found": (NotFoundError, "Slot non trovato"),
    "slot_wrong_progettista": (
        ConflictError,
        "Lo slot non appartiene al progettista assegnato",
    ),
    "slot_in_past": (ConflictError, "Lo slot è già passato: scegline uno futuro"),
    "slot_taken": (ConflictError, "Lo slot è appena stato prenotato: scegline un altro"),
    "booking_already_exists": (ConflictError, "Questa consulenza ha già un appuntamento"),
    "slot_booked": (ConflictError, "Lo slot è prenotato: non può essere modificato o eliminato"),
    "slot_overlap": (ConflictError, "Lo slot si sovrappone a un'altra tua disponibilità"),
    "serie_vuota": (BadRequestError, "La serie non contiene occorrenze"),
    "serie_troppo_lunga": (
        BadRequestError,
        "La serie contiene troppi slot: avvicina la data di fine",
    ),
    "serie_tutta_sovrapposta": (
        ConflictError,
        "Tutti gli slot della serie si sovrappongono alle tue disponibilità: nessuno slot creato",
    ),
    "serie_not_found": (NotFoundError, "Serie non trovata"),
    "user_not_found": (NotFoundError, "Utente non trovato"),
    # Creazione richiesta consulto (fn_create_consultation_request, 0028):
    "request_gia_aperta": (
        ConflictError,
        "C'è già una richiesta di consulto aperta per questo bando",
    ),
    "addon_credit_esaurito": (
        PaymentRequiredError,
        "Il consulto esperto si attiva con un acquisto: passa dal checkout",
    ),
    "addon_not_available": (
        NotFoundError,
        "Il consulto esperto non è al momento disponibile",
    ),
}


def raise_from_rpc(exc: APIError) -> NoReturn:
    """Traduce l'errore di una RPC 0017 (detail = codice macchina) in AppError."""
    detail = (exc.details or "").strip()
    mapped = _RPC_ERRORS.get(detail)
    if mapped:
        error_cls, message = mapped
        raise error_cls(message) from exc
    logger.error(
        "Errore RPC consulenze non mappato: code=%s detail=%s message=%s",
        exc.code,
        exc.details,
        exc.message,
    )
    raise UpstreamError() from exc


# Riferimenti forti ai task email in background (pattern ai_check_service:
# senza, il GC può cancellare un task in volo).
_background_tasks: set = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _frontend_link(path: str) -> str:
    return f"{get_settings().frontend_url.rstrip('/')}{path}"


def _videocall_url(token: str) -> str:
    """Stanza Jitsi dell'appuntamento: il TOKEN è il dato persistito, l'URL è
    derivato. L'istanza è aperta, quindi l'URL è di fatto una credenziale:
    viaggia nelle risposte API e nelle email, MAI nelle notifiche conservate."""
    return f"{get_settings().jitsi_base_url.rstrip('/')}/bandofit-{token}"


def _format_quando(dt: datetime) -> str:
    """Orario per le email: sempre Europe/Rome, con il fuso dichiarato."""
    rome = dt.astimezone(ZoneInfo("Europe/Rome"))
    return f"{rome.strftime('%d/%m/%Y')} alle {rome.strftime('%H:%M')} (ora italiana)"


async def _audit(primary, actor_id: str, action: str, *, target_user_id=None,
                 family_parent_id=None, payload: dict | None = None) -> None:
    """Best-effort: un guasto sull'audit non deve far fallire l'operazione."""
    try:
        await primary.table("audit_log").insert(
            {
                "actor_id": str(actor_id),
                "action": action,
                "target_user_id": str(target_user_id) if target_user_id else None,
                "family_parent_id": str(family_parent_id) if family_parent_id else None,
                "payload": payload or {},
            }
        ).execute()
    except Exception:
        logger.warning("audit_log non scrivibile per %s", action, exc_info=True)


# ---------------------------------------------------------------------------
# Slot di disponibilità (CRUD del progettista)
# ---------------------------------------------------------------------------


def _validate_slot_times(data: SlotIn) -> None:
    if data.fine <= data.inizio:
        raise BadRequestError("L'ora di fine deve seguire quella di inizio")
    durata = data.fine - data.inizio
    if durata < MIN_SLOT:
        raise BadRequestError("La durata minima di uno slot è di 15 minuti")
    if durata > MAX_SLOT:
        raise BadRequestError("La durata massima di uno slot è di 12 ore")
    if data.inizio <= datetime.now(timezone.utc):
        raise BadRequestError("Lo slot deve essere nel futuro")


async def _booked_slot_ids(primary, slot_ids: list[str]) -> set[str]:
    if not slot_ids:
        return set()
    resp = (
        await primary.table("consultation_bookings")
        .select("slot_id")
        .in_("slot_id", slot_ids)
        .eq("stato", "confermata")
        .execute()
    )
    return {row["slot_id"] for row in resp.data}


async def list_slots(primary, progettista_id: str) -> list[SlotOut]:
    """Slot non ancora conclusi (i passati escono da soli, nessun job di pulizia)."""
    resp = (
        await primary.table("availability_slots")
        .select(SLOT_SELECT)
        .eq("progettista_id", progettista_id)
        .gte("fine", datetime.now(timezone.utc).isoformat())
        .order("inizio")
        .execute()
    )
    booked = await _booked_slot_ids(primary, [row["id"] for row in resp.data])
    return [SlotOut(**row, prenotato=row["id"] in booked) for row in resp.data]


async def create_slot(primary, progettista_id: str, data: SlotIn) -> SlotOut:
    _validate_slot_times(data)
    try:
        resp = (
            await primary.table("availability_slots")
            .insert(
                {
                    "progettista_id": progettista_id,
                    "inizio": data.inizio.isoformat(),
                    "fine": data.fine.isoformat(),
                }
            )
            .execute()
        )
    except APIError as exc:
        if exc.code == _EXCLUSION_VIOLATION:
            raise ConflictError(
                "Lo slot si sovrappone a un'altra tua disponibilità"
            ) from exc
        raise
    row = resp.data[0]
    return SlotOut(
        id=row["id"],
        inizio=row["inizio"],
        fine=row["fine"],
        prenotato=False,
        serie_id=row.get("serie_id"),
    )


async def update_slot(primary, progettista_id: str, slot_id: str, data: SlotIn) -> SlotOut:
    _validate_slot_times(data)
    try:
        # RPC con FOR UPDATE: un update condizionale non vedrebbe una
        # prenotazione committata durante l'attesa del lock (READ COMMITTED).
        await primary.rpc(
            "fn_update_slot",
            {
                "p_slot_id": str(slot_id),
                "p_progettista_id": progettista_id,
                "p_inizio": data.inizio.isoformat(),
                "p_fine": data.fine.isoformat(),
            },
        ).execute()
    except APIError as exc:
        raise_from_rpc(exc)
    # Il PATCH non stacca lo slot dalla sua serie: si rilegge il serie_id per
    # non mentire nella risposta (la RPC 0017 ritorna void).
    sel = (
        await primary.table("availability_slots")
        .select("serie_id")
        .eq("id", str(slot_id))
        .limit(1)
        .execute()
    )
    serie_id = sel.data[0]["serie_id"] if sel.data else None
    return SlotOut(
        id=slot_id, inizio=data.inizio, fine=data.fine, prenotato=False, serie_id=serie_id
    )


async def delete_slot(primary, progettista_id: str, slot_id: str) -> None:
    try:
        await primary.rpc(
            "fn_delete_slot",
            {"p_slot_id": str(slot_id), "p_progettista_id": progettista_id},
        ).execute()
    except APIError as exc:
        raise_from_rpc(exc)


async def create_slot_serie(primary, progettista_id: str, data: SerieIn) -> SerieCreateOut:
    """Serie di ricorrenza: le occorrenze arrivano già materializzate dal
    browser. Ognuna viene validata come uno slot singolo PRIMA della RPC
    (400 senza scritture); quelle sovrapposte le salta la RPC, riga per riga."""
    for occorrenza in data.occorrenze:
        _validate_slot_times(occorrenza)
    try:
        resp = await primary.rpc(
            "fn_create_slot_serie",
            {
                "p_progettista_id": progettista_id,
                "p_occorrenze": [
                    {"inizio": occ.inizio.isoformat(), "fine": occ.fine.isoformat()}
                    for occ in data.occorrenze
                ],
            },
        ).execute()
    except APIError as exc:
        raise_from_rpc(exc)
    result = resp.data
    return SerieCreateOut(
        serie_id=result["serie_id"],
        creati=[SlotOut(**row, prenotato=False) for row in result["creati"]],
        saltati=result["saltati"],
    )


async def delete_slot_serie(primary, progettista_id: str, serie_id: str) -> SerieDeleteOut:
    """Elimina gli slot LIBERI della serie; i prenotati non si toccano mai."""
    try:
        resp = await primary.rpc(
            "fn_delete_slot_serie",
            {"p_serie_id": str(serie_id), "p_progettista_id": progettista_id},
        ).execute()
    except APIError as exc:
        raise_from_rpc(exc)
    return SerieDeleteOut(**resp.data)


# ---------------------------------------------------------------------------
# Eventi: notifica in-app (affidabile, per prima) + email best-effort in
# background. Le notifiche conservate NON contengono dati personali del
# cliente (minimizzazione): i dettagli si leggono seguendo l'url.
# ---------------------------------------------------------------------------


async def _send_emails_best_effort(sends: list) -> None:
    """Riceve FACTORY (partial), non coroutine: la coroutine nasce solo quando
    il task gira davvero — niente «coroutine was never awaited» se il task
    viene cancellato prima di partire."""
    for send in sends:
        try:
            await send()
        except Exception:  # pragma: no cover - email_service non solleva mai
            logger.warning("invio email consulenze fallito", exc_info=True)


async def _event_nuova_richiesta(primary, request: dict) -> None:
    """Evento 1: a tutti i progettisti e admin attivi (parità admin, 0019)."""
    resp = (
        await primary.table("profiles")
        .select("id,email")
        .in_("role", ["progettista", "admin"])
        .eq("is_active", True)
        .execute()
    )
    progettisti = resp.data or []
    await notification_service.notify(
        primary,
        [row["id"] for row in progettisti],
        tipo="consulenza.nuova_richiesta",
        titolo="Nuova richiesta di consulto",
        corpo=f"Bando: {request['bando_titolo']}",
        url="/app/progettista/richieste",
        dedup_key=f"richiesta:{request['id']}",
    )
    cta = _frontend_link("/app/progettista/richieste")
    _spawn(
        _send_emails_best_effort(
            [
                partial(
                    email_service.send_consulting_request_email,
                    row["email"],
                    request["bando_titolo"],
                    cta,
                )
                for row in progettisti
                if row.get("email")
            ]
        )
    )


async def _event_proposta_ricevuta(primary, request: dict, proposal_id: str, autore: str | None) -> None:
    """Evento 2: al titolare della richiesta. La notifica CONSERVATA non
    contiene il nome dell'autore (minimizzazione: solo il bando, i dettagli
    si leggono seguendo l'url); il nome viaggia nell'email, effimera."""
    await notification_service.notify(
        primary,
        [request["cliente_id"]],
        tipo="consulenza.proposta",
        titolo="Hai ricevuto una proposta di consulenza",
        corpo=f"Bando: {request['bando_titolo']}",
        url=f"/app/consulenze/{request['id']}",
        dedup_key=f"proposta:{proposal_id}",
    )
    cliente = (
        await primary.table("profiles")
        .select("email")
        .eq("id", request["cliente_id"])
        .limit(1)
        .execute()
    )
    if cliente.data and cliente.data[0].get("email"):
        _spawn(
            _send_emails_best_effort(
                [
                    partial(
                        email_service.send_proposal_email,
                        cliente.data[0]["email"],
                        autore or "Un progettista",
                        request["bando_titolo"],
                        _frontend_link(f"/app/consulenze/{request['id']}"),
                    )
                ]
            )
        )


async def _progettista_email(primary, progettista_id: str) -> str | None:
    resp = (
        await primary.table("profiles")
        .select("email")
        .eq("id", progettista_id)
        .limit(1)
        .execute()
    )
    return resp.data[0].get("email") if resp.data else None


async def _ragione_sociale(primary, company_profile_id: str) -> str | None:
    resp = (
        await primary.table("company_profiles")
        .select("ragione_sociale")
        .eq("id", company_profile_id)
        .limit(1)
        .execute()
    )
    return resp.data[0].get("ragione_sociale") if resp.data else None


async def _event_assegnazione(primary, request: dict, progettista_id: str) -> None:
    """Evento 4: al progettista assegnato."""
    await notification_service.notify(
        primary,
        [progettista_id],
        tipo="consulenza.assegnazione",
        titolo="Ti è stata assegnata una consulenza",
        corpo=f"Bando: {request['bando_titolo']}",
        url=f"/app/progettista/richieste/{request['id']}",
        dedup_key=f"assegnazione:{request['id']}",
    )
    email = await _progettista_email(primary, progettista_id)
    if email:
        ragione = await _ragione_sociale(primary, request["company_profile_id"])
        _spawn(
            _send_emails_best_effort(
                [
                    partial(
                        email_service.send_assignment_email,
                        email,
                        ragione or "Un'azienda",
                        request["bando_titolo"],
                        _frontend_link(f"/app/progettista/richieste/{request['id']}"),
                    )
                ]
            )
        )


async def _event_prenotazione(primary, request: dict, booking: dict) -> None:
    """Evento 3: notifica al progettista + email col link videochiamata a
    ENTRAMBI (conferma al cliente inclusa). Il link Jitsi viaggia SOLO nelle
    email, effimere: l'istanza è aperta e l'URL è una credenziale, quindi
    non entra mai nel corpo delle notifiche conservate (minimizzazione)."""
    inizio = datetime.fromisoformat(booking["inizio"])
    await notification_service.notify(
        primary,
        [booking["progettista_id"]],
        tipo="consulenza.prenotazione",
        titolo="Nuova consulenza prenotata",
        corpo=f"{request['bando_titolo']} — {_format_quando(inizio)}",
        url=f"/app/progettista/richieste/{request['id']}",
        dedup_key=f"booking:{booking['id']}",
    )

    token = booking.get("videocall_token")
    videocall_url = _videocall_url(token) if token else None
    sends = []
    email = await _progettista_email(primary, booking["progettista_id"])
    if email:
        ragione = await _ragione_sociale(primary, request["company_profile_id"])
        sends.append(
            partial(
                email_service.send_booking_email,
                email,
                ragione or "Un'azienda",
                _format_quando(inizio),
                _frontend_link(f"/app/progettista/richieste/{request['id']}"),
                videocall_url,
            )
        )
    cliente = (
        await primary.table("profiles")
        .select("email")
        .eq("id", booking["cliente_id"])
        .limit(1)
        .execute()
    )
    if cliente.data and cliente.data[0].get("email"):
        sends.append(
            partial(
                email_service.send_booking_confirmation_email,
                cliente.data[0]["email"],
                _format_quando(inizio),
                videocall_url,
                _frontend_link(f"/app/consulenze/{request['id']}"),
            )
        )
    if sends:
        _spawn(_send_emails_best_effort(sends))


# ---------------------------------------------------------------------------
# Lato cliente
# ---------------------------------------------------------------------------


async def _fetch_request(primary, request_id: str) -> dict | None:
    resp = (
        await primary.table("consultation_requests")
        .select(REQUEST_SELECT)
        .eq("id", str(request_id))
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


async def _fetch_request_for_family(primary, user: dict, request_id: str) -> tuple[dict, bool]:
    """Richiesta visibile alla famiglia dell'utente; editable = può agire.
    Un membro ATTIVO vede solo le consulenze delle aziende a lui VISIBILI
    (0031): stessa regola del resolver dell'azienda attiva, 404 fuori insieme."""
    owner_id, editable = await family_service.owner_and_editable(primary, user)
    request = await _fetch_request(primary, request_id)
    if request is None or request["family_parent_id"] != owner_id:
        raise NotFoundError("Consulenza non trovata")
    visibili = await family_service.visible_company_ids_for_member(primary, user)
    if visibili is not None and str(request.get("company_profile_id")) not in visibili:
        raise NotFoundError("Consulenza non trovata")
    return request, editable


async def _codici_progettisti(primary, progettista_ids: list[str]) -> dict[str, str]:
    ids = [str(pid) for pid in progettista_ids if pid]
    if not ids:
        return {}
    resp = (
        await primary.table("progettisti")
        .select("user_id,codice")
        .in_("user_id", ids)
        .execute()
    )
    return {row["user_id"]: row["codice"] for row in resp.data}


async def _nomi_progettisti(primary, progettista_ids: list[str]) -> dict[str, str]:
    """Nome e cognome degli autori, in batch: il cliente vede le PERSONE,
    non i codici (il codice resta per gli usi interni/admin)."""
    ids = [str(pid) for pid in progettista_ids if pid]
    if not ids:
        return {}
    resp = (
        await primary.table("profiles")
        .select("id,nome,cognome")
        .in_("id", ids)
        .execute()
    )
    nomi: dict[str, str] = {}
    for row in resp.data:
        nome = " ".join(filter(None, [row.get("nome"), row.get("cognome")]))
        if nome:
            nomi[row["id"]] = nome
    return nomi


async def _bookings_by_request(primary, request_ids: list[str]) -> dict[str, dict]:
    if not request_ids:
        return {}
    resp = (
        await primary.table("consultation_bookings")
        .select(BOOKING_SELECT)
        .in_("request_id", request_ids)
        .eq("stato", "confermata")
        .execute()
    )
    return {row["request_id"]: row for row in resp.data}


def _map_booking(row: dict | None) -> BookingOut | None:
    if not row:
        return None
    token = row.get("videocall_token")
    return BookingOut(
        id=row["id"],
        inizio=row["inizio"],
        fine=row["fine"],
        stato=row["stato"],
        videocall_url=_videocall_url(token) if token else None,
    )


async def _progettista_pubblico(
    primary, request: dict, codici: dict[str, str]
) -> ProgettistaPublicOut | None:
    """L'assegnato, per il cliente: nome e cognome (la UI mostra quelli;
    il codice resta nel payload per gli usi interni)."""
    assigned = request.get("assigned_progettista_id")
    if not assigned:
        return None
    nome = None
    resp = (
        await primary.table("profiles")
        .select("nome,cognome")
        .eq("id", assigned)
        .limit(1)
        .execute()
    )
    if resp.data:
        nome = " ".join(
            filter(None, [resp.data[0].get("nome"), resp.data[0].get("cognome")])
        ) or None
    return ProgettistaPublicOut(codice=codici.get(assigned), nome=nome)


async def create_request(primary, user: dict, ai_check_id: str) -> ConsulenzaOut:
    """Attivazione dell'addon «Consulto esperto» su un AI-check completato.

    PUNTO D'INNESTO DEL PAGAMENTO: quando l'acquisto dell'addon diventerà
    reale, il checkout si inserisce in questa funzione, tra la verifica
    dell'addon e l'insert — la richiesta nasce solo a pagamento riuscito.
    Tutte le vie di creazione passano da qui (unico consumer del POST).
    """
    owner_id, editable = await family_service.owner_and_editable(primary, user)
    if not editable:
        raise ForbiddenError(
            "Solo il titolare dell'azienda può richiedere un consulto"
        )

    check_resp = (
        await primary.table("ai_checks")
        .select(
            "id,status,esito,punteggio,bando_id,bando_slug,bando_titolo,"
            "company_profile_id,family_parent_id"
        )
        .eq("id", str(ai_check_id))
        .eq("family_parent_id", owner_id)
        .limit(1)
        .execute()
    )
    if not check_resp.data:
        raise NotFoundError("AI-check non trovato")
    check = check_resp.data[0]
    if check["status"] != "ready":
        raise ConflictError("Il consulto si richiede su un AI-check completato")

    addon_resp = (
        await primary.table("addons")
        .select("id,slug,prezzo,tipo_prezzo,tipo_fruizione,is_active")
        .eq("slug", get_settings().consulting_addon_slug)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    if not addon_resp.data:
        raise NotFoundError("Il consulto esperto non è al momento disponibile")
    addon = addon_resp.data[0]

    # ---- gating consumabile a pagamento (modulo 0028) ----
    # Pre-check di CORTESIA: dà un 402 chiaro senza toccare il DB quando il
    # saldo è a zero. L'arbitro vero è la RPC (consumo atomico all'insert):
    # senza credito la fn_create_consultation_request solleva addon_credit_esaurito
    # annullando anche la richiesta. Il flusso gratis non è gatato.
    consumabile_a_pagamento = (
        addon.get("tipo_fruizione") == "consumabile"
        and addon.get("tipo_prezzo") == "importo"
        and Decimal(str(addon.get("prezzo") or "0")) > 0
    )
    if consumabile_a_pagamento:
        inv = (
            await primary.table("user_addon_inventory")
            .select("quantita")
            .eq("user_id", str(user["id"]))
            .eq("addon_id", addon["id"])
            .gt("quantita", 0)
            .limit(1)
            .execute()
        )
        if not inv.data:
            raise PaymentRequiredError(
                "Il consulto esperto si attiva con un acquisto: passa dal checkout"
            )

    # Insert richiesta + consumo di 1 unità in un'unica transazione (RPC 0028).
    try:
        resp = await primary.rpc(
            "fn_create_consultation_request",
            {"p_payload": {
                "cliente_id": str(user["id"]),
                "family_parent_id": owner_id,
                "company_profile_id": check["company_profile_id"],
                "ai_check_id": check["id"],
                "esito": check.get("esito"),
                "punteggio": check.get("punteggio"),
                "bando_id": check["bando_id"],
                "bando_slug": check["bando_slug"],
                "bando_titolo": check["bando_titolo"],
                "addon_id": addon["id"],
            }},
        ).execute()
    except APIError as exc:
        raise_from_rpc(exc)
    request = (resp.data or {}).get("request")
    if not request:
        raise UpstreamError()

    await _audit(
        primary,
        user["id"],
        "consulenza.created",
        family_parent_id=owner_id,
        payload={
            "request_id": request["id"],
            "bando_id": request["bando_id"],
            "bando_slug": request["bando_slug"],
            "ai_check_id": check["id"],
            "addon_slug": addon["slug"],
        },
    )
    await _event_nuova_richiesta(primary, request)
    return await get_my_request(primary, user, request["id"])


async def list_my_requests(primary, user: dict) -> list[ConsulenzaOut]:
    owner_id, editable = await family_service.owner_and_editable(primary, user)
    query = (
        primary.table("consultation_requests")
        .select(REQUEST_SELECT)
        .eq("family_parent_id", owner_id)
    )
    # Un membro ATTIVO elenca solo le consulenze delle aziende visibili (0031).
    visibili = await family_service.visible_company_ids_for_member(primary, user)
    if visibili is not None:
        if not visibili:
            return []
        query = query.in_("company_profile_id", visibili)
    resp = await query.order("created_at", desc=True).execute()
    rows = resp.data
    request_ids = [row["id"] for row in rows]

    aperte_per_request: dict[str, int] = {}
    if request_ids:
        proposte = (
            await primary.table("consultation_proposals")
            .select("request_id")
            .in_("request_id", request_ids)
            .eq("stato", "inviata")
            .execute()
        )
        for row in proposte.data:
            aperte_per_request[row["request_id"]] = (
                aperte_per_request.get(row["request_id"], 0) + 1
            )

    bookings = await _bookings_by_request(primary, request_ids)
    codici = await _codici_progettisti(
        primary, [row.get("assigned_progettista_id") for row in rows]
    )

    items = []
    for row in rows:
        items.append(
            ConsulenzaOut(
                id=row["id"],
                stato=row["stato"],
                bando_id=row["bando_id"],
                bando_slug=row["bando_slug"],
                bando_titolo=row["bando_titolo"],
                esito=row.get("esito"),
                punteggio=row.get("punteggio"),
                created_at=row["created_at"],
                assigned_at=row.get("assigned_at"),
                editable=editable,
                progettista=await _progettista_pubblico(primary, row, codici),
                proposte_aperte=aperte_per_request.get(row["id"], 0),
                appuntamento=_map_booking(bookings.get(row["id"])),
            )
        )
    return items


async def get_my_request(primary, user: dict, request_id: str) -> ConsulenzaOut:
    request, editable = await _fetch_request_for_family(primary, user, request_id)

    proposte_resp = (
        await primary.table("consultation_proposals")
        .select(PROPOSAL_SELECT)
        .eq("request_id", request["id"])
        .order("created_at", desc=True)
        .execute()
    )
    proposte_rows = proposte_resp.data
    autori = [row["progettista_id"] for row in proposte_rows]
    codici = await _codici_progettisti(
        primary, autori + [request.get("assigned_progettista_id")]
    )
    nomi = await _nomi_progettisti(primary, autori)
    bookings = await _bookings_by_request(primary, [request["id"]])

    return ConsulenzaOut(
        id=request["id"],
        stato=request["stato"],
        bando_id=request["bando_id"],
        bando_slug=request["bando_slug"],
        bando_titolo=request["bando_titolo"],
        esito=request.get("esito"),
        punteggio=request.get("punteggio"),
        created_at=request["created_at"],
        assigned_at=request.get("assigned_at"),
        editable=editable,
        progettista=await _progettista_pubblico(primary, request, codici),
        proposte_aperte=sum(1 for row in proposte_rows if row["stato"] == "inviata"),
        proposte=[
            ProposalOut(
                id=row["id"],
                codice_progettista=codici.get(row["progettista_id"]),
                nome_progettista=nomi.get(row["progettista_id"]),
                messaggio=row["messaggio"],
                stato=row["stato"],
                created_at=row["created_at"],
            )
            for row in proposte_rows
        ],
        appuntamento=_map_booking(bookings.get(request["id"])),
    )


def _require_actor(request: dict, user: dict) -> None:
    if request["cliente_id"] != str(user["id"]):
        raise ForbiddenError(
            "Solo il titolare dell'azienda può gestire questa consulenza"
        )


async def accept_proposal(
    primary, user: dict, request_id: str, proposal_id: str, slot_id: str | None
) -> ConsulenzaOut:
    try:
        resp = await primary.rpc(
            "fn_accept_proposal",
            {
                "p_request_id": str(request_id),
                "p_proposal_id": str(proposal_id),
                "p_cliente_id": str(user["id"]),
                "p_slot_id": str(slot_id) if slot_id else None,
            },
        ).execute()
    except APIError as exc:
        raise_from_rpc(exc)
    result = resp.data or {}

    request = await _fetch_request(primary, request_id)
    if request:
        await _event_assegnazione(primary, request, result["progettista_id"])
        if result.get("booking_id"):
            booking_resp = (
                await primary.table("consultation_bookings")
                .select(BOOKING_SELECT)
                .eq("id", result["booking_id"])
                .limit(1)
                .execute()
            )
            if booking_resp.data:
                await _event_prenotazione(primary, request, booking_resp.data[0])
    return await get_my_request(primary, user, request_id)


async def reject_proposal(
    primary, user: dict, request_id: str, proposal_id: str
) -> ConsulenzaOut:
    request, _ = await _fetch_request_for_family(primary, user, request_id)
    _require_actor(request, user)
    resp = (
        await primary.table("consultation_proposals")
        .update({"stato": "rifiutata"})
        .eq("id", str(proposal_id))
        .eq("request_id", request["id"])
        .eq("stato", "inviata")
        .execute()
    )
    if not resp.data:
        raise ConflictError("La proposta non è più disponibile")
    return await get_my_request(primary, user, request_id)


async def cancel_request(primary, user: dict, request_id: str) -> ConsulenzaOut:
    request, _ = await _fetch_request_for_family(primary, user, request_id)
    _require_actor(request, user)
    resp = (
        await primary.table("consultation_requests")
        .update({"stato": "annullata"})
        .eq("id", request["id"])
        .eq("stato", "nuova")
        .execute()
    )
    if not resp.data:
        raise ConflictError("La richiesta non è più aperta")

    # Le proposte aperte si chiudono come «superate» e i loro autori vengono
    # avvisati in-app (fuori dai 4 eventi: niente email).
    proposte = (
        await primary.table("consultation_proposals")
        .select("progettista_id")
        .eq("request_id", request["id"])
        .eq("stato", "inviata")
        .execute()
    )
    if proposte.data:
        await primary.table("consultation_proposals").update({"stato": "superata"}).eq(
            "request_id", request["id"]
        ).eq("stato", "inviata").execute()
        await notification_service.notify(
            primary,
            [row["progettista_id"] for row in proposte.data],
            tipo="consulenza.richiesta_annullata",
            titolo="Richiesta di consulto annullata",
            corpo=f"Bando: {request['bando_titolo']}",
            url="/app/progettista/richieste",
            dedup_key=f"richiesta-annullata:{request['id']}",
        )

    await _audit(
        primary,
        user["id"],
        "consulenza.cancelled",
        family_parent_id=request["family_parent_id"],
        payload={"request_id": request["id"]},
    )
    return await get_my_request(primary, user, request_id)


async def list_bookable_slots(
    primary, user: dict, request_id: str, proposta_id: str | None
) -> list[SlotOut]:
    """Slot liberi e futuri del progettista assegnato o di quello della
    proposta indicata (per prenotare contestualmente all'accettazione)."""
    request, _ = await _fetch_request_for_family(primary, user, request_id)
    if request["stato"] == "assegnata":
        progettista_id = request["assigned_progettista_id"]
    elif proposta_id:
        proposta = (
            await primary.table("consultation_proposals")
            .select("progettista_id,stato")
            .eq("id", str(proposta_id))
            .eq("request_id", request["id"])
            .limit(1)
            .execute()
        )
        if not proposta.data or proposta.data[0]["stato"] != "inviata":
            raise NotFoundError("Proposta non trovata o non più disponibile")
        progettista_id = proposta.data[0]["progettista_id"]
    else:
        raise BadRequestError("Indica la proposta di cui vedere le disponibilità")

    resp = (
        await primary.table("availability_slots")
        .select(SLOT_SELECT)
        .eq("progettista_id", progettista_id)
        .gt("inizio", datetime.now(timezone.utc).isoformat())
        .order("inizio")
        .execute()
    )
    booked = await _booked_slot_ids(primary, [row["id"] for row in resp.data])
    return [
        SlotOut(**row, prenotato=False)
        for row in resp.data
        if row["id"] not in booked
    ]


async def book_slot(primary, user: dict, request_id: str, slot_id: str) -> ConsulenzaOut:
    try:
        resp = await primary.rpc(
            "fn_book_slot",
            {
                "p_request_id": str(request_id),
                "p_slot_id": str(slot_id),
                "p_actor_id": str(user["id"]),
            },
        ).execute()
    except APIError as exc:
        raise_from_rpc(exc)
    booking_id = resp.data

    request = await _fetch_request(primary, request_id)
    if request and booking_id:
        booking_resp = (
            await primary.table("consultation_bookings")
            .select(BOOKING_SELECT)
            .eq("id", booking_id)
            .limit(1)
            .execute()
        )
        if booking_resp.data:
            await _event_prenotazione(primary, request, booking_resp.data[0])
    return await get_my_request(primary, user, request_id)


async def cancel_booking(primary, user: dict, request_id: str) -> ConsulenzaOut:
    request, _ = await _fetch_request_for_family(primary, user, request_id)
    _require_actor(request, user)
    booking = await _cancel_booking_row(primary, request_id=request["id"])
    await notification_service.notify(
        primary,
        [booking["progettista_id"]],
        tipo="consulenza.prenotazione_annullata",
        titolo="Appuntamento annullato",
        corpo=f"Bando: {request['bando_titolo']}",
        url=f"/app/progettista/richieste/{request['id']}",
        dedup_key=f"appuntamento-annullato:{booking['id']}",
    )
    await _audit(
        primary,
        user["id"],
        "consulenza.booking_cancelled",
        target_user_id=booking["progettista_id"],
        family_parent_id=request["family_parent_id"],
        payload={"request_id": request["id"], "booking_id": booking["id"]},
    )
    return await get_my_request(primary, user, request_id)


async def _cancel_booking_row(primary, *, request_id: str) -> dict:
    """Annulla la prenotazione confermata della richiesta (per il titolare,
    che agisce per consulenza); lo slot torna prenotabile da solo
    (l'indice parziale copre solo stato='confermata')."""
    resp = (
        await primary.table("consultation_bookings")
        .update({"stato": "annullata"})
        .eq("request_id", str(request_id))
        .eq("stato", "confermata")
        .execute()
    )
    if not resp.data:
        raise ConflictError("Nessun appuntamento confermato da annullare")
    return resp.data[0]


# ---------------------------------------------------------------------------
# Lato progettista
# ---------------------------------------------------------------------------


async def _partial_context(primary, rows: list[dict]) -> tuple[dict, dict]:
    """Dati PARZIALI (requisito punto 3) per una lista di richieste, in query
    batch: ragione sociale + P.IVA dall'azienda, denominazione ed email dal
    profilo del titolare. Niente select * : solo questi campi."""
    company_ids = list({row["company_profile_id"] for row in rows})
    companies: dict[str, dict] = {}
    if company_ids:
        resp = (
            await primary.table("company_profiles")
            .select("id,ragione_sociale,partita_iva")
            .in_("id", company_ids)
            .execute()
        )
        companies = {row["id"]: row for row in resp.data}

    cliente_ids = list({row["cliente_id"] for row in rows})
    profili: dict[str, dict] = {}
    if cliente_ids:
        resp = (
            await primary.table("profiles")
            .select("id,nome,cognome,email,azienda")
            .in_("id", cliente_ids)
            .execute()
        )
        profili = {row["id"]: row for row in resp.data}
    return companies, profili


def _denominazione(profilo: dict | None, company: dict | None) -> str:
    """Stessa scala di family_service.parent_display_name, ma in batch."""
    if company and company.get("ragione_sociale"):
        return company["ragione_sociale"]
    if profilo:
        if profilo.get("azienda"):
            return profilo["azienda"]
        full_name = " ".join(filter(None, [profilo.get("nome"), profilo.get("cognome")]))
        if full_name:
            return full_name
        if profilo.get("email"):
            return profilo["email"]
    return "il titolare"


def _map_pool_row(
    row: dict,
    companies: dict,
    profili: dict,
    *,
    progettista_id: str,
    mia_proposta: str | None,
    booking: dict | None,
) -> RichiestaPoolOut:
    company = companies.get(row["company_profile_id"])
    profilo = profili.get(row["cliente_id"])
    return RichiestaPoolOut(
        id=row["id"],
        stato=row["stato"],
        ragione_sociale=(company or {}).get("ragione_sociale"),
        partita_iva=(company or {}).get("partita_iva"),
        denominazione_utente=_denominazione(profilo, company),
        email=(profilo or {}).get("email"),
        bando_id=row["bando_id"],
        bando_slug=row["bando_slug"],
        bando_titolo=row["bando_titolo"],
        esito=row.get("esito"),
        punteggio=row.get("punteggio"),
        created_at=row["created_at"],
        assegnata_a_me=row.get("assigned_progettista_id") == progettista_id,
        mia_proposta_stato=mia_proposta,
        appuntamento=_map_booking(booking),
    )


async def _mie_proposte_per_request(
    primary, progettista_id: str, request_ids: list[str]
) -> dict[str, str]:
    """Stato della proposta più recente del progettista per ogni richiesta."""
    if not request_ids:
        return {}
    resp = (
        await primary.table("consultation_proposals")
        .select("request_id,stato,created_at")
        .eq("progettista_id", progettista_id)
        .in_("request_id", request_ids)
        .order("created_at", desc=True)
        .execute()
    )
    latest: dict[str, str] = {}
    for row in resp.data:
        latest.setdefault(row["request_id"], row["stato"])
    return latest


async def list_pool(primary, progettista: dict) -> RichiestePoolResponse:
    progettista_id = str(progettista["id"])
    aperte_resp = (
        await primary.table("consultation_requests")
        .select(REQUEST_SELECT)
        .eq("stato", "nuova")
        .order("created_at", desc=True)
        .execute()
    )
    assegnate_resp = (
        await primary.table("consultation_requests")
        .select(REQUEST_SELECT)
        .eq("assigned_progettista_id", progettista_id)
        .order("created_at", desc=True)
        .execute()
    )
    rows = aperte_resp.data + assegnate_resp.data
    companies, profili = await _partial_context(primary, rows)
    mie = await _mie_proposte_per_request(
        primary, progettista_id, [row["id"] for row in rows]
    )
    bookings = await _bookings_by_request(
        primary, [row["id"] for row in assegnate_resp.data]
    )

    def mapper(row: dict) -> RichiestaPoolOut:
        return _map_pool_row(
            row,
            companies,
            profili,
            progettista_id=progettista_id,
            mia_proposta=mie.get(row["id"]),
            booking=bookings.get(row["id"]),
        )

    return RichiestePoolResponse(
        aperte=[mapper(row) for row in aperte_resp.data],
        assegnate=[mapper(row) for row in assegnate_resp.data],
    )


async def _fetch_request_for_progettista(primary, progettista: dict, request_id: str) -> dict:
    """Una richiesta è visibile al progettista se è nel pool (nuova) o se è
    assegnata a lui. Le altre non esistono (404, niente leak)."""
    request = await _fetch_request(primary, str(request_id))
    if request is None:
        raise NotFoundError("Richiesta non trovata")
    if request["stato"] != "nuova" and request.get("assigned_progettista_id") != str(
        progettista["id"]
    ):
        raise NotFoundError("Richiesta non trovata")
    return request


async def get_pool_request(
    primary, progettista: dict, request_id: str
) -> RichiestaPoolDetailOut:
    request = await _fetch_request_for_progettista(primary, progettista, request_id)
    companies, profili = await _partial_context(primary, [request])

    ai_check = None
    if request.get("ai_check_id"):
        check_resp = (
            await primary.table("ai_checks")
            .select(
                "id,bando_id,bando_slug,bando_titolo,status,error_detail,esito,"
                "punteggio,tipo_punteggio,model,extraction_cached,created_at,"
                "ready_at,report"
            )
            .eq("id", request["ai_check_id"])
            .limit(1)
            .execute()
        )
        if check_resp.data:
            # Stessa serializzazione del percorso cliente (_to_out): applica
            # lo scrub in lettura dei report storici (domini esclusi).
            ai_check = ai_check_service._to_out(check_resp.data[0], include_report=True)

    proposte_resp = (
        await primary.table("consultation_proposals")
        .select(PROPOSAL_SELECT)
        .eq("request_id", request["id"])
        .eq("progettista_id", str(progettista["id"]))
        .order("created_at", desc=True)
        .execute()
    )
    codici = await _codici_progettisti(primary, [str(progettista["id"])])
    bookings = await _bookings_by_request(primary, [request["id"]])
    mia_stato = proposte_resp.data[0]["stato"] if proposte_resp.data else None

    base = _map_pool_row(
        request,
        companies,
        profili,
        progettista_id=str(progettista["id"]),
        mia_proposta=mia_stato,
        booking=bookings.get(request["id"]),
    )
    return RichiestaPoolDetailOut(
        **base.model_dump(),
        ai_check=ai_check,
        mie_proposte=[
            ProposalOut(
                id=row["id"],
                codice_progettista=codici.get(row["progettista_id"]),
                messaggio=row["messaggio"],
                stato=row["stato"],
                created_at=row["created_at"],
            )
            for row in proposte_resp.data
        ],
    )


async def create_proposal(
    primary, progettista: dict, request_id: str, messaggio: str
) -> RichiestaPoolDetailOut:
    request = await _fetch_request_for_progettista(primary, progettista, request_id)
    if request["stato"] != "nuova":
        raise ConflictError("La richiesta non è più aperta")

    # Parità admin: alla prima proposta l'admin riceve pigramente il codice
    # PRG (senza cambio ruolo, RPC 0019), così l'evento 2 («dal Progettista
    # {codice}») e la UI del titolare lo mostrano da subito.
    if progettista.get("role") == "admin":
        try:
            await primary.rpc(
                "fn_ensure_progettista_codice", {"p_user_id": str(progettista["id"])}
            ).execute()
        except APIError as exc:
            raise_from_rpc(exc)

    try:
        resp = (
            await primary.table("consultation_proposals")
            .insert(
                {
                    "request_id": request["id"],
                    "progettista_id": str(progettista["id"]),
                    "messaggio": messaggio,
                }
            )
            .execute()
        )
    except APIError as exc:
        if exc.code == _UNIQUE_VIOLATION:
            raise ConflictError(
                "Hai già una proposta aperta su questa richiesta"
            ) from exc
        raise
    proposal = resp.data[0]

    # La finestra guardia→insert non è serializzata (due chiamate PostgREST
    # in transazioni separate): un'accettazione o un annullo committati nel
    # mezzo lascerebbero questa proposta «inviata» per sempre — orfana e non
    # più accettabile. Ricontrollo e compensazione: la proposta si chiude
    # come «superata» e il titolare NON viene notificato.
    request_after = await _fetch_request(primary, request["id"])
    if request_after is None or request_after["stato"] != "nuova":
        await primary.table("consultation_proposals").update(
            {"stato": "superata"}
        ).eq("id", proposal["id"]).eq("stato", "inviata").execute()
        raise ConflictError("La richiesta non è più aperta")

    await _audit(
        primary,
        progettista["id"],
        "consulenza.proposal_sent",
        family_parent_id=request["family_parent_id"],
        payload={"request_id": request["id"], "proposal_id": proposal["id"]},
    )
    # L'autore per il cliente è una persona, non un codice: nome e cognome
    # arrivano dal profilo già caricato in CurrentUser.
    autore = " ".join(
        filter(None, [progettista.get("nome"), progettista.get("cognome")])
    ) or None
    await _event_proposta_ricevuta(primary, request, proposal["id"], autore)
    return await get_pool_request(primary, progettista, request_id)


async def withdraw_proposal(primary, progettista: dict, proposal_id: str) -> None:
    resp = (
        await primary.table("consultation_proposals")
        .update({"stato": "ritirata"})
        .eq("id", str(proposal_id))
        .eq("progettista_id", str(progettista["id"]))
        .eq("stato", "inviata")
        .execute()
    )
    if not resp.data:
        raise ConflictError("La proposta non è più ritirabile")


async def get_full_company(
    primary, progettista: dict, request_id: str
) -> FullCompanyOut:
    """Vista FULL (decisione #4): tutti i dati azienda + dossier certificato.
    Doppia guardia nel service (ruolo già garantito dal router, qui
    l'assegnazione) e log di OGNI accesso in audit_log."""
    request = await _fetch_request(primary, str(request_id))
    if request is None or request.get("assigned_progettista_id") != str(progettista["id"]):
        raise ForbiddenError("Il dossier completo è visibile solo al progettista assegnato")

    company = await company_service.get_company_for_owner(
        primary, request["family_parent_id"]
    )
    dossier = await openapi_service.get_dossier_for_owner(
        primary, request["family_parent_id"]
    )

    await _audit(
        primary,
        progettista["id"],
        "consulenza.dossier_accessed",
        target_user_id=request["cliente_id"],
        family_parent_id=request["family_parent_id"],
        payload={"request_id": request["id"]},
    )
    return FullCompanyOut(company=company, dossier=dossier)


async def list_appointments(primary, progettista: dict) -> list[AppuntamentoOut]:
    resp = (
        await primary.table("consultation_bookings")
        .select(BOOKING_SELECT)
        .eq("progettista_id", str(progettista["id"]))
        .eq("stato", "confermata")
        .order("inizio")
        .execute()
    )
    rows = resp.data
    request_ids = list({row["request_id"] for row in rows})
    requests: dict[str, dict] = {}
    if request_ids:
        req_resp = (
            await primary.table("consultation_requests")
            .select("id,bando_titolo,company_profile_id,cliente_id")
            .in_("id", request_ids)
            .execute()
        )
        requests = {row["id"]: row for row in req_resp.data}
    companies, profili = await _partial_context(
        primary,
        [
            {"company_profile_id": r["company_profile_id"], "cliente_id": r["cliente_id"]}
            for r in requests.values()
        ],
    )

    items = []
    for row in rows:
        request = requests.get(row["request_id"]) or {}
        company = companies.get(request.get("company_profile_id"))
        profilo = profili.get(request.get("cliente_id"))
        items.append(
            AppuntamentoOut(
                id=row["id"],
                request_id=row["request_id"],
                inizio=row["inizio"],
                fine=row["fine"],
                stato=row["stato"],
                bando_titolo=request.get("bando_titolo") or "—",
                ragione_sociale=(company or {}).get("ragione_sociale"),
                email=(profilo or {}).get("email"),
                videocall_url=(
                    _videocall_url(row["videocall_token"])
                    if row.get("videocall_token")
                    else None
                ),
            )
        )
    return items


async def progettista_cancel_booking(
    primary, progettista: dict, booking_id: str
) -> None:
    # Update condizionale ATOMICO sul booking indicato: senza il filtro su
    # stato, un id di un appuntamento già annullato finirebbe per annullare
    # la prenotazione CONFERMATA della stessa richiesta (un altro booking).
    resp = (
        await primary.table("consultation_bookings")
        .update({"stato": "annullata"})
        .eq("id", str(booking_id))
        .eq("progettista_id", str(progettista["id"]))
        .eq("stato", "confermata")
        .execute()
    )
    if not resp.data:
        raise NotFoundError("Appuntamento confermato non trovato")
    booking = resp.data[0]
    request = await _fetch_request(primary, booking["request_id"])
    if request:
        await notification_service.notify(
            primary,
            [booking["cliente_id"]],
            tipo="consulenza.prenotazione_annullata",
            titolo="Appuntamento annullato dal progettista",
            corpo=f"Bando: {request['bando_titolo']}",
            url=f"/app/consulenze/{request['id']}",
            dedup_key=f"appuntamento-annullato:{booking['id']}",
        )
        await _audit(
            primary,
            progettista["id"],
            "consulenza.booking_cancelled",
            target_user_id=booking["cliente_id"],
            family_parent_id=request["family_parent_id"],
            payload={"request_id": request["id"], "booking_id": booking["id"]},
        )
