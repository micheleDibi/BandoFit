"""Operatività admin del modulo partenariati (WP3): estrazioni delle regole
per bando, forzatura e run manuale dello scheduler. Flag sul router (404 a
flag spento, anche senza token); ogni rotta richiede un admin."""

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import (
    AdminUser,
    AiDep,
    PrimaryClient,
    SecondaryClient,
    RottaPartenariati,
    require_partenariati_attivo,
)
from app.core.config import get_settings
from app.core.errors import ConflictError
from app.schemas.common import Page
from app.schemas.partenariato import (
    EstrazioneAdminOut,
    ForzaEstrazioneIn,
    PartenariatiRunOut,
    PartenariatoBandoOut,
)
from app.services import partenariati_scheduler, partenariato_service

router = APIRouter(
    prefix="/admin/partenariati",
    tags=["admin"],
    dependencies=[Depends(require_partenariati_attivo)],
    route_class=RottaPartenariati,
)

ADMIN_SELECT = (
    "bando_id,bando_slug,bando_titolo,stato,esito,fase,modalita_effettiva,model,cost_cents,"
    "input_tokens,output_tokens,estratta_at,verificata_at,ultima_esecuzione_at,errore_codice,"
    "tentativi_falliti,prossimo_tentativo_at,updated_at"
)


def _testo(valore) -> str | None:
    return str(valore) if valore is not None else None


@router.get("/estrazioni", response_model=Page[EstrazioneAdminOut])
async def list_estrazioni(
    user: AdminUser,
    primary: PrimaryClient,
    stato: Literal["in_corso", "pronta", "errore"] | None = None,
    esito: Literal["estratta", "nessun_segnale"] | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> Page[EstrazioneAdminOut]:
    """Estrazioni per bando, dalla più recente (mai claim né output grezzo)."""
    query = primary.table("bando_partenariato").select(ADMIN_SELECT, count="exact")
    if stato:
        query = query.eq("stato", stato)
    if esito:
        query = query.eq("esito", esito)
    offset = (page - 1) * page_size
    resp = (
        await query.order("updated_at", desc=True)
        .range(offset, offset + page_size - 1)
        .execute()
    )
    items = [
        EstrazioneAdminOut(
            **{
                **riga,
                **{
                    campo: _testo(riga.get(campo))
                    for campo in (
                        "estratta_at", "verificata_at", "ultima_esecuzione_at",
                        "prossimo_tentativo_at", "updated_at",
                    )
                },
            }
        )
        for riga in resp.data or []
    ]
    return Page.build(items, resp.count or 0, page, page_size)


@router.post("/estrazioni/{bando_id}", response_model=PartenariatoBandoOut)
async def forza_estrazione(
    bando_id: int,
    response: Response,
    user: AdminUser,
    primary: PrimaryClient,
    secondary: SecondaryClient,
    ai: AiDep,
    payload: ForzaEstrazioneIn | None = None,
) -> PartenariatoBandoOut:
    """Nuova estrazione del bando (paga sempre il modello, stesso budget
    giornaliero). 202 se partita, 200 se già in corso."""
    stato, avviata = await partenariato_service.forza_admin(
        primary,
        secondary,
        ai,
        user,
        bando_id,
        ignora_cooldown=bool(payload and payload.ignora_cooldown),
    )
    response.status_code = 202 if avviata else 200
    return stato


@router.post("/run", response_model=PartenariatiRunOut)
async def run_scheduler(
    user: AdminUser,
    primary: PrimaryClient,
    secondary: SecondaryClient,
    ai: AiDep,
    ripeti: bool = False,
) -> PartenariatiRunOut:
    """Esegue subito la run di oggi. Senza `ripeti` rispetta il claim (409 se
    già eseguita); con `ripeti=true` riesegue (i passi sono idempotenti e la
    spesa resta nel budget del giorno)."""
    oggi = datetime.now(ZoneInfo(get_settings().alert_fuso)).date()
    if not ripeti and not await partenariati_scheduler.claim_run(primary, oggi):
        raise ConflictError("La run di oggi è già stata eseguita (usa ripeti=true)")
    riepilogo = await partenariati_scheduler.esegui_run(primary, secondary, ai, oggi)
    return PartenariatiRunOut(giorno=oggi.isoformat(), riepilogo=riepilogo)
