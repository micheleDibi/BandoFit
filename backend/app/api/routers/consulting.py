from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, PrimaryClient
from app.schemas.consulting import (
    AcceptProposalIn,
    BookIn,
    ConsulenzaOut,
    CreateRequestIn,
    SlotOut,
)
from app.services import consulting_service

router = APIRouter(prefix="/me/consulenze", tags=["consulenze"])


@router.get("", response_model=list[ConsulenzaOut])
async def list_requests(user: CurrentUser, primary: PrimaryClient) -> list[ConsulenzaOut]:
    return await consulting_service.list_my_requests(primary, user)


@router.post("", response_model=ConsulenzaOut, status_code=201)
async def create_request(
    data: CreateRequestIn, user: CurrentUser, primary: PrimaryClient
) -> ConsulenzaOut:
    return await consulting_service.create_request(primary, user, str(data.ai_check_id))


@router.get("/{request_id}", response_model=ConsulenzaOut)
async def get_request(
    request_id: UUID, user: CurrentUser, primary: PrimaryClient
) -> ConsulenzaOut:
    return await consulting_service.get_my_request(primary, user, str(request_id))


@router.post("/{request_id}/annulla", response_model=ConsulenzaOut)
async def cancel_request(
    request_id: UUID, user: CurrentUser, primary: PrimaryClient
) -> ConsulenzaOut:
    return await consulting_service.cancel_request(primary, user, str(request_id))


@router.get("/{request_id}/slots", response_model=list[SlotOut])
async def bookable_slots(
    request_id: UUID,
    user: CurrentUser,
    primary: PrimaryClient,
    proposta: UUID | None = Query(default=None),
) -> list[SlotOut]:
    return await consulting_service.list_bookable_slots(
        primary, user, str(request_id), str(proposta) if proposta else None
    )


@router.post("/{request_id}/proposte/{proposal_id}/accetta", response_model=ConsulenzaOut)
async def accept_proposal(
    request_id: UUID,
    proposal_id: UUID,
    data: AcceptProposalIn,
    user: CurrentUser,
    primary: PrimaryClient,
) -> ConsulenzaOut:
    return await consulting_service.accept_proposal(
        primary, user, str(request_id), str(proposal_id),
        str(data.slot_id) if data.slot_id else None,
    )


@router.post("/{request_id}/proposte/{proposal_id}/rifiuta", response_model=ConsulenzaOut)
async def reject_proposal(
    request_id: UUID,
    proposal_id: UUID,
    user: CurrentUser,
    primary: PrimaryClient,
) -> ConsulenzaOut:
    return await consulting_service.reject_proposal(
        primary, user, str(request_id), str(proposal_id)
    )


@router.post("/{request_id}/prenota", response_model=ConsulenzaOut, status_code=201)
async def book_slot(
    request_id: UUID, data: BookIn, user: CurrentUser, primary: PrimaryClient
) -> ConsulenzaOut:
    return await consulting_service.book_slot(
        primary, user, str(request_id), str(data.slot_id)
    )


@router.post("/{request_id}/prenotazione/annulla", response_model=ConsulenzaOut)
async def cancel_booking(
    request_id: UUID, user: CurrentUser, primary: PrimaryClient
) -> ConsulenzaOut:
    return await consulting_service.cancel_booking(primary, user, str(request_id))
