"""Rotte trasversali del modulo partenariati (flag sul router: 404 a flag
spento, anche senza token). WP3: il vocabolario controllato; WP4 aggiunge
l'informativa."""

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, RottaPartenariati, require_partenariati_attivo
from app.schemas.partenariato_vocabolario import VocabolarioOut
from app.services.partenariato_vocabolario import vocabolario_out

router = APIRouter(
    prefix="/partenariati",
    tags=["partenariati"],
    dependencies=[Depends(require_partenariati_attivo)],
    route_class=RottaPartenariati,
)


@router.get("/vocabolario", response_model=VocabolarioOut)
async def get_vocabolario(user: CurrentUser) -> VocabolarioOut:
    """Tipi di soggetto, competenze, forme di aggregazione e ruoli (v1)."""
    return vocabolario_out()
