"""Admin sul catalogo bandi: monitoraggio della raccolta (pannello «Catalogo»)."""

from fastapi import APIRouter

from app.api.deps import AdminUser, SecondaryClient
from app.schemas.catalogo_monitoraggio import MonitoraggioCatalogoOut
from app.services import catalogo_monitoraggio

router = APIRouter(prefix="/admin/catalogo", tags=["admin"])


@router.get("/monitoraggio", response_model=MonitoraggioCatalogoOut)
async def monitoraggio(user: AdminUser, secondary: SecondaryClient) -> MonitoraggioCatalogoOut:
    """Stato del monitoraggio del catalogo. Sempre 200: l'esito della lettura
    sta in `stato_accesso` (un errore del catalogo non diventa mai un 5xx);
    la busta c'è solo con `ok`. Cache di 60 s per processo."""
    return await catalogo_monitoraggio.leggi(secondary)
