from fastapi import APIRouter

from app.api.deps import CurrentUser, PrimaryClient
from app.schemas.entitlement import EntitlementsOut
from app.services import entitlement_service

router = APIRouter(prefix="/me", tags=["entitlements"])


@router.get("/entitlements", response_model=EntitlementsOut)
async def my_entitlements(user: CurrentUser, primary: PrimaryClient) -> EntitlementsOut:
    """Le quote dell'account (seats, aziende, AI-check) in un'unica risposta,
    calcolate dalla formula unica lato DB: il frontend legge da qui, non
    ricalcola mai. Per un collegato attivo sono le quote del titolare."""
    return await entitlement_service.get_entitlements(primary, user)
