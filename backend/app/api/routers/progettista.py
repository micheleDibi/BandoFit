from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import (
    PrimaryClient,
    ProgettistaUser,
    SecondaryClient,
    require_partenariati_attivo,
)
from app.schemas.consulting import (
    AppuntamentoOut,
    FullCompanyOut,
    ProposalIn,
    RichiestaPoolDetailOut,
    RichiestePoolResponse,
    SerieCreateOut,
    SerieDeleteOut,
    SerieIn,
    SlotIn,
    SlotOut,
)
from app.services import consulting_service
from app.services.partenariato_accesso import CallVistaProgettistaOut

router = APIRouter(prefix="/progettista", tags=["progettista"])


@router.get("/richieste", response_model=RichiestePoolResponse)
async def list_richieste(
    user: ProgettistaUser, primary: PrimaryClient
) -> RichiestePoolResponse:
    return await consulting_service.list_pool(primary, user)


@router.get("/richieste/{request_id}", response_model=RichiestaPoolDetailOut)
async def get_richiesta(
    request_id: UUID, user: ProgettistaUser, primary: PrimaryClient
) -> RichiestaPoolDetailOut:
    return await consulting_service.get_pool_request(primary, user, str(request_id))


@router.post(
    "/richieste/{request_id}/proposte",
    response_model=RichiestaPoolDetailOut,
    status_code=201,
)
async def create_proposal(
    request_id: UUID, data: ProposalIn, user: ProgettistaUser, primary: PrimaryClient
) -> RichiestaPoolDetailOut:
    return await consulting_service.create_proposal(
        primary, user, str(request_id), data.messaggio
    )


@router.post("/proposte/{proposal_id}/ritira", status_code=204)
async def withdraw_proposal(
    proposal_id: UUID, user: ProgettistaUser, primary: PrimaryClient
) -> None:
    await consulting_service.withdraw_proposal(primary, user, str(proposal_id))


@router.get("/richieste/{request_id}/dossier", response_model=FullCompanyOut)
async def get_full_company(
    request_id: UUID, user: ProgettistaUser, primary: PrimaryClient
) -> FullCompanyOut:
    return await consulting_service.get_full_company(primary, user, str(request_id))


# WP9 (partenariati): rotta aggiunta a un router ESISTENTE, quindi il flag sta
# sulla SINGOLA rotta (a flag spento 404, anche prima dell'autenticazione) e
# le altre rotte /progettista non cambiano (docs/partenariati.md T1).
@router.get(
    "/richieste/{request_id}/call",
    response_model=CallVistaProgettistaOut,
    dependencies=[Depends(require_partenariati_attivo)],
)
async def get_call_richiesta(
    request_id: UUID, user: ProgettistaUser, primary: PrimaryClient, secondary: SecondaryClient
) -> CallVistaProgettistaOut:
    """La call di partenariato di un consulto chiesto dalla call, per il
    progettista ASSEGNATO (404 per gli altri): proiezione dedicata senza
    contatti né messaggi, con l'accesso registrato (502 se la registrazione
    non riesce: nessun dato)."""
    return await consulting_service.get_call_per_progettista(
        primary, secondary, user, str(request_id)
    )


@router.get("/appuntamenti", response_model=list[AppuntamentoOut])
async def list_appointments(
    user: ProgettistaUser, primary: PrimaryClient
) -> list[AppuntamentoOut]:
    return await consulting_service.list_appointments(primary, user)


@router.post("/appuntamenti/{booking_id}/annulla", status_code=204)
async def cancel_booking(
    booking_id: UUID, user: ProgettistaUser, primary: PrimaryClient
) -> None:
    await consulting_service.progettista_cancel_booking(primary, user, str(booking_id))


@router.get("/slots", response_model=list[SlotOut])
async def list_slots(user: ProgettistaUser, primary: PrimaryClient) -> list[SlotOut]:
    return await consulting_service.list_slots(primary, user["id"])


@router.post("/slots", response_model=SlotOut, status_code=201)
async def create_slot(data: SlotIn, user: ProgettistaUser, primary: PrimaryClient) -> SlotOut:
    return await consulting_service.create_slot(primary, user["id"], data)


@router.patch("/slots/{slot_id}", response_model=SlotOut)
async def update_slot(
    slot_id: UUID, data: SlotIn, user: ProgettistaUser, primary: PrimaryClient
) -> SlotOut:
    return await consulting_service.update_slot(primary, user["id"], str(slot_id), data)


@router.delete("/slots/{slot_id}", status_code=204)
async def delete_slot(slot_id: UUID, user: ProgettistaUser, primary: PrimaryClient) -> None:
    await consulting_service.delete_slot(primary, user["id"], str(slot_id))


@router.post("/slots/serie", response_model=SerieCreateOut, status_code=201)
async def create_slot_serie(
    data: SerieIn, user: ProgettistaUser, primary: PrimaryClient
) -> SerieCreateOut:
    return await consulting_service.create_slot_serie(primary, user["id"], data)


@router.delete("/slots/serie/{serie_id}", response_model=SerieDeleteOut)
async def delete_slot_serie(
    serie_id: UUID, user: ProgettistaUser, primary: PrimaryClient
) -> SerieDeleteOut:
    # 200 con body (non 204): il conteggio eliminati/mantenuti serve alla UI.
    return await consulting_service.delete_slot_serie(primary, user["id"], str(serie_id))
