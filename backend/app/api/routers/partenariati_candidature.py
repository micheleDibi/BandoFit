"""Candidature spontanee e inviti delle call di partenariato (WP7,
docs/partenariati.md K1-K2, §5).

Router NUOVO del modulo: flag sul router e sulla route class (404 anche
senza token e con un corpo malformato, T1). Scrive solo il titolare
dell'azienda attiva; i membri con visibilità leggono. Una candidatura esiste
solo per le sue due parti: per chiunque altro (anche un'altra azienda dello
stesso owner) è 404. Verso l'altra parte mai `company_profile_id`: il
creatore vede le candidate per pseudonimo e le invita con lo pseudonimo.
"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps import (
    ActiveCompanyDep,
    CurrentUser,
    PrimaryClient,
    RottaPartenariati,
    SecondaryClient,
    require_partenariati_attivo,
)
from app.schemas.common import Page
from app.services import partenariato_candidature_service as candidature
from app.services.partenariato_candidature_service import (
    CandidaturaIn,
    CandidaturaOut,
    InvitoIn,
    RifiutoIn,
)

router = APIRouter(
    prefix="/partenariati",
    tags=["partenariati"],
    dependencies=[Depends(require_partenariati_attivo)],
    route_class=RottaPartenariati,
)


@router.post("/call/{call_id}/candidature", response_model=CandidaturaOut, status_code=201)
async def invia_candidatura(
    call_id: str,
    data: CandidaturaIn,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> CandidaturaOut:
    """Candidatura spontanea dell'azienda attiva (serve l'opt-in visibile e un
    piano con candidature). 201 con la quota del mese; 400
    `testo_non_conforme`; 409 `funzione_non_inclusa`,
    `profilo_partner_non_attivo`, `candidature_esaurite`, `stesso_gruppo`,
    `call_non_attiva`, `candidatura_gia_attiva`, …; 429 `limite_candidature`."""
    return await candidature.invia_candidatura(primary, secondary, active, user, call_id, data)


@router.post("/call/{call_id}/inviti", response_model=CandidaturaOut, status_code=201)
async def invita(
    call_id: str,
    data: InvitoIn,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> CandidaturaOut:
    """Invito del creatore a un'azienda suggerita, indicata dallo pseudonimo
    della call. 409 `partner_non_disponibile` (neutro), `invito_gia_attivo`,
    `inviti_esauriti_call`; 429 `limite_inviti`."""
    return await candidature.invita(primary, secondary, active, user, call_id, data)


@router.get("/candidature", response_model=Page[CandidaturaOut])
async def lista(
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
    direzione: Literal["inviate", "ricevute"] = Query("ricevute"),
    stato: Literal["inviata", "accettata", "rifiutata", "ritirata", "scaduta"] | None = Query(
        None),
    tipo: Literal["candidatura", "invito"] | None = Query(None),
    call_id: UUID | None = Query(None),
    page: int = Query(1, ge=1, le=100_000),
    page_size: int = Query(20, ge=1, le=50),
) -> Page[CandidaturaOut]:
    """Candidature e inviti inviati o ricevuti dall'azienda attiva (`call_id`:
    solo quelli di una call)."""
    return await candidature.lista(primary, secondary, active, user, direzione=direzione,
                                   stato=stato, tipo=tipo, call_id=call_id, page=page,
                                   page_size=page_size)


@router.get("/candidature/{candidatura_id}", response_model=CandidaturaOut)
async def dettaglio(
    candidatura_id: str,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> CandidaturaOut:
    return await candidature.dettaglio(primary, secondary, active, user, candidatura_id)


@router.post("/candidature/{candidatura_id}/accetta", response_model=CandidaturaOut)
async def accetta(
    candidatura_id: str,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> CandidaturaOut:
    """Accettazione (il creatore per le candidature, l'invitata per gli
    inviti): apre la conversazione (`conversazione_id`). 409
    `candidatura_gia_decisa`, `invito_scaduto`, `call_non_attiva`,
    `esclusivita_violata`, `controparte_non_disponibile`, …"""
    return await candidature.decidi(primary, secondary, active, user, candidatura_id, "accetta")


@router.post("/candidature/{candidatura_id}/rifiuta", response_model=CandidaturaOut)
async def rifiuta(
    candidatura_id: str,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
    data: RifiutoIn | None = None,
) -> CandidaturaOut:
    """Rifiuto con motivo facoltativo (≤ 500 caratteri, senza contatti)."""
    return await candidature.decidi(primary, secondary, active, user, candidatura_id, "rifiuta",
                                    data)


@router.post("/candidature/{candidatura_id}/ritira", response_model=CandidaturaOut)
async def ritira(
    candidatura_id: str,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> CandidaturaOut:
    """Ritiro della propria candidatura o del proprio invito, se in attesa."""
    return await candidature.ritira(primary, secondary, active, user, candidatura_id)
