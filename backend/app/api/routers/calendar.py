from fastapi import APIRouter, Query

from app.api.deps import ActiveCompanyDep, CurrentUser, PrimaryClient, SecondaryClient
from app.schemas.calendar import (
    CalendarBandoIn,
    CalendarEventIn,
    CalendarEventOut,
    CalendarEventsOut,
    CalendarEventUpdate,
)
from app.services import calendar_service

router = APIRouter(prefix="/me/calendar", tags=["calendar"])


@router.get("", response_model=CalendarEventsOut)
async def list_events(
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    anno: int = Query(ge=2000, le=2100),
    mese: int = Query(ge=1, le=12),
) -> CalendarEventsOut:
    return await calendar_service.list_events(primary, user["id"], active, anno, mese)


@router.post("", response_model=CalendarEventOut, status_code=201)
async def create_event(
    payload: CalendarEventIn,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
) -> CalendarEventOut:
    """Crea un evento personale."""
    return await calendar_service.create_event(primary, user["id"], active, payload)


@router.post("/bando", response_model=CalendarEventOut, status_code=201)
async def add_bando_deadline(
    payload: CalendarBandoIn,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> CalendarEventOut:
    """Aggiunge la scadenza di un bando al calendario (evento tipo 'bando',
    data derivata dal catalogo; idempotente)."""
    return await calendar_service.create_bando_event(
        primary, secondary, user["id"], active, payload.bando_slug
    )


@router.patch("/{event_id}", response_model=CalendarEventOut)
async def update_event(
    event_id: str,
    payload: CalendarEventUpdate,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
) -> CalendarEventOut:
    return await calendar_service.update_event(primary, user["id"], active, event_id, payload)


@router.delete("/{event_id}", status_code=204)
async def delete_event(
    event_id: str, user: CurrentUser, active: ActiveCompanyDep, primary: PrimaryClient
) -> None:
    await calendar_service.delete_event(primary, user["id"], active, event_id)
