from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.api.deps import CurrentUser, PrimaryClient, SecondaryClient
from app.schemas.common import Page
from app.schemas.saved_bando import SavedBandoItem, SavedIdsOut, SaveBandoIn
from app.services import saved_bandi_service

router = APIRouter(prefix="/me/saved-bandi", tags=["saved-bandi"])


@router.post("", response_model=SavedBandoItem, status_code=201)
async def save_bando(
    payload: SaveBandoIn,
    user: CurrentUser,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> SavedBandoItem:
    """Salva un bando tra i preferiti (idempotente: già salvato → lo ritorna)."""
    return await saved_bandi_service.save_bando(
        primary, secondary, user["id"], payload.bando_slug
    )


# Dichiarato PRIMA di eventuali rotte parametriche: /ids non è un bando_id.
@router.get("/ids", response_model=SavedIdsOut)
async def saved_ids(user: CurrentUser, primary: PrimaryClient) -> SavedIdsOut:
    return await saved_bandi_service.saved_ids(primary, user["id"])


@router.get("", response_model=Page[SavedBandoItem])
async def list_saved(
    user: CurrentUser,
    primary: PrimaryClient,
    secondary: SecondaryClient,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
) -> Page[SavedBandoItem]:
    return await saved_bandi_service.list_saved(primary, secondary, user["id"], page, page_size)


@router.delete("/{bando_id}", status_code=204)
async def remove_bando(
    # Limiti di int4: un id fuori range manderebbe PostgREST in 22003 (→ 502).
    bando_id: Annotated[int, Path(ge=1, le=2_147_483_647)],
    user: CurrentUser,
    primary: PrimaryClient,
) -> None:
    """Rimuove dai preferiti (idempotente; l'eventuale evento in calendario resta)."""
    await saved_bandi_service.remove_bando(primary, user["id"], bando_id)
