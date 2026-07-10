"""Consulenze (DB primario, service_role): slot di disponibilità del
progettista e — nelle fasi successive — flusso richiesta → proposta →
assegnazione → prenotazione.

La concorrenza vive a livello DB (migration 0017): qui si validano gli input,
si chiamano le RPC e si traducono i loro detail-code in errori HTTP, con la
stessa meccanica di family_service.raise_from_rpc.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import NoReturn

from postgrest.exceptions import APIError

from app.core.errors import (
    AppError,
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UpstreamError,
)
from app.schemas.consulting import SlotIn, SlotOut

logger = logging.getLogger("bandofit.consulting")

# Sanity check sulla durata: la durata è libera (decisione di dominio), questi
# limiti intercettano solo refusi (slot di 2 minuti o di 3 giorni).
MIN_SLOT = timedelta(minutes=15)
MAX_SLOT = timedelta(hours=12)

SLOT_SELECT = "id,inizio,fine"

# Codice PostgreSQL della violazione di un EXCLUDE constraint (sovrapposizione).
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
    return SlotOut(id=row["id"], inizio=row["inizio"], fine=row["fine"], prenotato=False)


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
    return SlotOut(id=slot_id, inizio=data.inizio, fine=data.fine, prenotato=False)


async def delete_slot(primary, progettista_id: str, slot_id: str) -> None:
    try:
        await primary.rpc(
            "fn_delete_slot",
            {"p_slot_id": str(slot_id), "p_progettista_id": progettista_id},
        ).execute()
    except APIError as exc:
        raise_from_rpc(exc)
