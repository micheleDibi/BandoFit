"""Operatività admin sugli alert nuovi-bandi: run manuale e registro."""

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query

from app.api.deps import AdminUser, PrimaryClient, SecondaryClient
from app.core.config import get_settings
from app.core.errors import ConflictError
from app.schemas.alerts import AlertRunOut, AlertRunRiepilogoOut
from app.services import alert_scheduler, bando_alert_service

router = APIRouter(prefix="/admin/alerts", tags=["admin"])


@router.post("/run", response_model=AlertRunRiepilogoOut)
async def run_alerts(
    user: AdminUser,
    primary: PrimaryClient,
    secondary: SecondaryClient,
    ripeti: bool = False,
) -> AlertRunRiepilogoOut:
    """Esegue subito la run di oggi. Senza `ripeti` rispetta il claim (409 se
    già eseguita); con `ripeti=true` riesegue — il ledger per (utente, bando)
    impedisce comunque i doppi invii."""
    settings = get_settings()
    oggi = datetime.now(ZoneInfo(settings.alert_fuso)).date()
    if not ripeti and not await alert_scheduler.claim_run(primary, oggi):
        raise ConflictError("La run di oggi è già stata eseguita (usa ripeti=true)")
    riepilogo = await bando_alert_service.esegui_run(primary, secondary, oggi)
    return AlertRunRiepilogoOut(**riepilogo)


@router.get("/runs", response_model=list[AlertRunOut])
async def list_runs(
    user: AdminUser,
    primary: PrimaryClient,
    limit: int = Query(default=30, ge=1, le=100),
) -> list[AlertRunOut]:
    resp = (
        await primary.table("bando_alert_runs")
        .select("*")
        .order("giorno", desc=True)
        .limit(limit)
        .execute()
    )
    return [AlertRunOut(**row) for row in resp.data or []]
