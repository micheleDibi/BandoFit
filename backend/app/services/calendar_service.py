"""Calendario personale (vista mensile, eventi per utente).

Due tipi di evento: 'personale' (CRUD completo) e 'bando' (scadenza derivata
dal catalogo secondario: data in SOLA LETTURA — modificabili solo titolo e
note; il riferimento è denormalizzato come nei bandi salvati, senza FK).
Le date/ore sono di calendario italiano (wall-clock), mai convertite.
"""

import logging
import uuid
from datetime import date

from postgrest.exceptions import APIError

from app.core.errors import BadRequestError, NotFoundError
from app.schemas.calendar import (
    CalendarEventIn,
    CalendarEventOut,
    CalendarEventsOut,
    CalendarEventUpdate,
)
from app.services import company_scope

logger = logging.getLogger("bandofit.calendar")

MAX_EVENTS = 500
# Campi modificabili su un evento di tipo 'bando' (la data è la scadenza
# ufficiale del catalogo: non si sposta da qui).
BANDO_EVENT_EDITABLE = {"titolo", "note"}

EVENT_SELECT = (
    "id,titolo,data,tutto_il_giorno,ora_inizio,ora_fine,note,tipo,"
    "bando_id,bando_slug,created_at,updated_at"
)
SNAPSHOT_SELECT = "id,slug,titolo,titolo_breve,data_scadenza"


def _to_out(row: dict) -> CalendarEventOut:
    return CalendarEventOut(**{**row, "id": str(row["id"])})


def _month_bounds(anno: int, mese: int) -> tuple[str, str]:
    first = date(anno, mese, 1)
    next_first = date(anno + 1, 1, 1) if mese == 12 else date(anno, mese + 1, 1)
    return first.isoformat(), next_first.isoformat()


def _ensure_uuid(event_id: str) -> str:
    """Ritorna l'UUID NORMALIZZATO: Python accetta forme (urn:uuid:, graffe)
    che Postgres rifiuterebbe con 22P02 (→ 502) — la query deve usare la
    forma canonica, non la stringa grezza."""
    try:
        return str(uuid.UUID(str(event_id)))
    except ValueError:
        # Un id malformato è, per il chiamante, un evento inesistente.
        raise NotFoundError("Evento non trovato") from None


async def _check_cap(primary, user_id: str, active) -> None:
    resp = (
        await company_scope.filter_read(
            primary.table("calendar_events")
            .select("id", count="exact")
            .eq("user_id", str(user_id)),
            active,
        )
        .limit(1)
        .execute()
    )
    if (resp.count or 0) >= MAX_EVENTS:
        raise BadRequestError(
            f"Hai raggiunto il limite di {MAX_EVENTS} eventi in calendario: "
            "eliminane qualcuno per crearne altri"
        )


async def list_events(primary, user_id: str, active, anno: int, mese: int) -> CalendarEventsOut:
    """Eventi del mese richiesto per l'azienda attiva (mai il DB secondario)."""
    start, end = _month_bounds(anno, mese)
    resp = (
        await company_scope.filter_read(
            primary.table("calendar_events")
            .select(EVENT_SELECT)
            .eq("user_id", str(user_id))
            .gte("data", start)
            .lt("data", end),
            active,
        )
        .order("data")
        .order("ora_inizio", nullsfirst=True)  # "tutto il giorno" in testa
        .order("created_at")
        .execute()
    )
    return CalendarEventsOut(items=[_to_out(row) for row in (resp.data or [])])


async def create_event(
    primary, user_id: str, active, payload: CalendarEventIn
) -> CalendarEventOut:
    """Crea un evento PERSONALE (il tipo non arriva mai dal client)."""
    await _check_cap(primary, user_id, active)
    row = {
        "user_id": str(user_id),
        "company_profile_id": company_scope.scope_value(active),
        "titolo": payload.titolo.strip(),
        "data": payload.data.isoformat(),
        "tutto_il_giorno": payload.tutto_il_giorno,
        "ora_inizio": payload.ora_inizio.isoformat() if payload.ora_inizio else None,
        "ora_fine": payload.ora_fine.isoformat() if payload.ora_fine else None,
        "note": payload.note,
        "tipo": "personale",
    }
    insert = await primary.table("calendar_events").insert(row).execute()
    return _to_out(insert.data[0])


async def create_bando_event(
    primary, secondary, user_id: str, active, slug: str
) -> CalendarEventOut:
    """Aggiunge al calendario dell'azienda attiva la SCADENZA di un bando
    (evento tipo 'bando', data derivata dal catalogo). Idempotente: se l'evento
    esiste già per questo bando lo ritorna. Non richiede che il bando sia salvato."""
    resp = (
        await secondary.table("bando")
        .select(SNAPSHOT_SELECT)
        .eq("slug", slug)
        .eq("stato_processing", "completed")
        .limit(1)
        .execute()
    )
    if not resp.data:
        raise NotFoundError("Bando non trovato")
    bando = resp.data[0]
    if not bando.get("data_scadenza"):
        raise BadRequestError("Questo bando non ha una data di scadenza da aggiungere")

    existing = (
        await company_scope.filter_read(
            primary.table("calendar_events")
            .select(EVENT_SELECT)
            .eq("user_id", str(user_id))
            .eq("tipo", "bando")
            .eq("bando_id", bando["id"]),
            active,
        )
        .limit(1)
        .execute()
    )
    if existing.data:
        return _to_out(existing.data[0])

    await _check_cap(primary, user_id, active)
    titolo = bando.get("titolo_breve") or bando.get("titolo") or bando["slug"]
    row = {
        "user_id": str(user_id),
        "company_profile_id": company_scope.scope_value(active),
        "titolo": f"Scadenza: {titolo}"[:200],
        "data": bando["data_scadenza"],
        "tutto_il_giorno": True,
        "tipo": "bando",
        "bando_id": bando["id"],
        "bando_slug": bando["slug"],
    }
    try:
        insert = await primary.table("calendar_events").insert(row).execute()
        return _to_out(insert.data[0])
    except APIError as exc:
        if exc.code != "23505":
            raise
        # Corsa tra due click: l'indice unico parziale ha deciso, rileggiamo.
        retry = (
            await company_scope.filter_read(
                primary.table("calendar_events")
                .select(EVENT_SELECT)
                .eq("user_id", str(user_id))
                .eq("tipo", "bando")
                .eq("bando_id", bando["id"]),
                active,
            )
            .limit(1)
            .execute()
        )
        if not retry.data:  # pragma: no cover — solo per robustezza
            raise
        return _to_out(retry.data[0])


async def _fetch_event(primary, user_id: str, active, event_id: str) -> dict:
    normalized = _ensure_uuid(event_id)
    resp = (
        await company_scope.filter_read(
            primary.table("calendar_events")
            .select(EVENT_SELECT)
            .eq("id", normalized)
            .eq("user_id", str(user_id)),  # mai eventi di altri utenti
            active,
        )
        .limit(1)
        .execute()
    )
    if not resp.data:
        raise NotFoundError("Evento non trovato")
    return resp.data[0]


async def update_event(
    primary, user_id: str, active, event_id: str, patch: CalendarEventUpdate
) -> CalendarEventOut:
    row = await _fetch_event(primary, user_id, active, event_id)
    changes = patch.model_dump(exclude_unset=True)
    if not changes:
        return _to_out(row)

    if row["tipo"] == "bando":
        blocked = set(changes) - BANDO_EVENT_EDITABLE
        if blocked:
            raise BadRequestError(
                "Per gli eventi legati a un bando puoi modificare solo titolo e note: "
                "la data è la scadenza ufficiale del bando"
            )

    # Orari su un evento che resta "tutto il giorno": la rivalidazione li
    # azzererebbe in silenzio (200 senza effetto) — meglio dirlo chiaramente.
    merged_all_day = changes.get("tutto_il_giorno", row["tutto_il_giorno"])
    if merged_all_day and any(
        changes.get(key) is not None for key in ("ora_inizio", "ora_fine")
    ):
        raise BadRequestError(
            "L'evento è segnato come tutto il giorno: per impostare gli orari "
            "togli prima la spunta (tutto_il_giorno = false)"
        )

    # La coerenza degli orari ha un'unica fonte: CalendarEventIn rivalida il
    # merge (e azzera gli orari quando si passa a "tutto il giorno").
    merged = {
        "titolo": changes.get("titolo", row["titolo"]),
        "data": changes.get("data", row["data"]),
        "tutto_il_giorno": changes.get("tutto_il_giorno", row["tutto_il_giorno"]),
        "ora_inizio": changes.get("ora_inizio", row.get("ora_inizio")),
        "ora_fine": changes.get("ora_fine", row.get("ora_fine")),
        "note": changes.get("note", row.get("note")),
    }
    try:
        valid = CalendarEventIn.model_validate(merged)
    except ValueError as exc:
        # I messaggi dei nostri validator sono in italiano ("Value error, …");
        # gli errori di tipo grezzi di pydantic sono in inglese → generico.
        raw = str(exc.errors()[0]["msg"])
        message = (
            raw.removeprefix("Value error, ")
            if raw.startswith("Value error, ")
            else "Dati dell'evento non validi: controlla i campi e riprova"
        )
        raise BadRequestError(message) from None

    update = (
        await company_scope.filter_read(
            primary.table("calendar_events")
            .update(
                {
                    "titolo": valid.titolo.strip(),
                    "data": valid.data.isoformat(),
                    "tutto_il_giorno": valid.tutto_il_giorno,
                    "ora_inizio": valid.ora_inizio.isoformat() if valid.ora_inizio else None,
                    "ora_fine": valid.ora_fine.isoformat() if valid.ora_fine else None,
                    "note": valid.note,
                }
            )
            .eq("id", str(event_id))
            .eq("user_id", str(user_id)),
            active,
        ).execute()
    )
    if update.data:
        return _to_out(update.data[0])
    return _to_out(await _fetch_event(primary, user_id, active, event_id))


async def delete_event(primary, user_id: str, active, event_id: str) -> None:
    """Elimina l'evento dell'azienda attiva. Per gli eventi 'bando' NON tocca
    il bando salvato (indipendenti)."""
    normalized = _ensure_uuid(event_id)
    resp = (
        await company_scope.filter_read(
            primary.table("calendar_events")
            .delete()
            .eq("id", normalized)
            .eq("user_id", str(user_id)),
            active,
        ).execute()
    )
    if not resp.data:
        raise NotFoundError("Evento non trovato")
