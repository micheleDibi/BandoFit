"""Regole di partenariato del bando (WP3): stato e avvio dell'estrazione.

Router NUOVO del modulo partenariati: il flag sta sul router, quindi a flag
spento ogni rotta risponde 404 anche senza token (docs/partenariati.md T1).
Stesso prefisso del catalogo (`/bandi`), path a due segmenti: nessuna
collisione con `GET /bandi/{slug}`.
"""

from fastapi import APIRouter, Depends, Response

from app.api.deps import (
    ActiveCompanyDep,
    AiDep,
    CurrentUser,
    PrimaryClient,
    SecondaryClient,
    RottaPartenariati,
    require_partenariati_attivo,
)
from app.schemas.partenariato import AvviaAnalisiIn, PartenariatoBandoOut
from app.services import partenariato_service

router = APIRouter(
    prefix="/bandi",
    tags=["partenariati"],
    dependencies=[Depends(require_partenariati_attivo)],
    route_class=RottaPartenariati,
)


@router.get("/{slug}/partenariato", response_model=PartenariatoBandoOut)
async def get_partenariato(
    slug: str,
    user: CurrentUser,
    primary: PrimaryClient,
    secondary: SecondaryClient,
    ai: AiDep,
) -> PartenariatoBandoOut:
    """Stato delle regole di partenariato del bando (poll-on-read)."""
    return await partenariato_service.get_stato(primary, secondary, slug, ai=ai)


@router.post("/{slug}/partenariato/analisi", response_model=PartenariatoBandoOut)
async def avvia_analisi_partenariato(
    slug: str,
    response: Response,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
    ai: AiDep,
    payload: AvviaAnalisiIn | None = None,
) -> PartenariatoBandoOut:
    """Avvia l'estrazione (costo della piattaforma, in background): 202 se è
    partita ora, 200 se il risultato è ancora fresco o se è già in corso."""
    stato, avviata = await partenariato_service.avvia_analisi(
        primary,
        secondary,
        ai,
        user,
        active,
        slug,
        forza=bool(payload and payload.forza),
    )
    response.status_code = 202 if avviata else 200
    return stato
