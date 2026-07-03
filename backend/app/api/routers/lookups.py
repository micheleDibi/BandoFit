from fastapi import APIRouter, Response

from app.api.deps import CurrentUser, SecondaryClient
from app.schemas.bando import LookupsOut
from app.services import lookup_service

router = APIRouter(prefix="/lookups", tags=["lookups"])


@router.get("", response_model=LookupsOut)
async def get_lookups(
    _user: CurrentUser, secondary: SecondaryClient, response: Response
) -> LookupsOut:
    """Valori delle faccette di filtro (regioni, settori, ...). Cambiano di rado."""
    response.headers["Cache-Control"] = "private, max-age=3600"
    return await lookup_service.get_lookups(secondary)
