from uuid import UUID

from fastapi import APIRouter

from app.api.deps import PrimaryClient, ProgettistaUser
from app.schemas.consulting import SlotIn, SlotOut
from app.services import consulting_service

router = APIRouter(prefix="/progettista", tags=["progettista"])


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
