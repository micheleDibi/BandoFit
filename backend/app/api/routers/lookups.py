from fastapi import APIRouter, Response

from app.api.deps import CurrentUser, SecondaryClient
from app.schemas.bando import LookupsOut
from app.services import lookup_service

router = APIRouter(prefix="/lookups", tags=["lookups"])


@router.get("", response_model=LookupsOut)
async def get_lookups(
    _user: CurrentUser, secondary: SecondaryClient, response: Response
) -> LookupsOut:
    """Valori delle faccette di filtro (regioni, settori, ...). Cambiano di rado.
    In degrado (catalogo non leggibile: cache scaduta o liste vuote) la
    risposta non va tenuta dal browser: il server ritenta dopo un minuto."""
    lookups = await lookup_service.get_lookups(secondary, degrada=True)
    response.headers["Cache-Control"] = (
        "no-store" if lookup_service.in_degrado() else "private, max-age=3600"
    )
    return lookups
