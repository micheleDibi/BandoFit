from uuid import UUID

from fastapi import APIRouter

from app.api.deps import PrimaryClient, ProgettistaUser
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
