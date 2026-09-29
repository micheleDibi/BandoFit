"""Call di partenariato (WP5, docs/partenariati.md §2.5 e §5).

Router NUOVO del modulo partenariati: il flag sta sul router e sulla route
class, quindi a flag spento ogni rotta risponde 404 anche senza token e con un
corpo malformato (T1). Scrive solo il titolare dell'azienda attiva; i membri
con visibilità leggono; tutto ciò che non è dell'azienda attiva è 404 (T3).
Le proposte AI sono job asincroni: 202 e poll sul dettaglio (T7).
"""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from app.api.deps import (
    ActiveCompanyDep,
    AiDep,
    CurrentUser,
    PrimaryClient,
    RottaPartenariati,
    SecondaryClient,
    require_partenariati_attivo,
)
from app.schemas.common import Page
from app.schemas.partner_call import (
    AnteprimaOut,
    CallAggiornaIn,
    CallCardOut,
    CallCreaIn,
    CallVistaCreatoreOut,
    ChiudiIn,
    GapOut,
    JobPosizioniOut,
    JobTestiOut,
    PosizioniIn,
    RegoleConfermaIn,
    RequisitiIn,
    VersioneOut,
)
from app.services import partner_call_service

router = APIRouter(
    prefix="/partenariati/call",
    tags=["partenariati"],
    dependencies=[Depends(require_partenariati_attivo)],
    route_class=RottaPartenariati,
)


class PubblicaIn(BaseModel):
    """POST /partenariati/call/{id}/pubblica: scadenza facoltativa (default
    `min(scadenza del bando, oggi + 60 giorni)`)."""

    model_config = ConfigDict(extra="forbid")

    scadenza_call: date | None = None


@router.get("", response_model=Page[CallCardOut])
async def lista_call(
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
    vista: Literal["mie"] = Query("mie"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
) -> Page[CallCardOut]:
    """Le call dell'azienda attiva (`vista=mie`; le altre viste arrivano con
    il WP6)."""
    return await partner_call_service.lista_mie(
        primary, secondary, active, user, page=page, page_size=page_size
    )


@router.post("", response_model=CallVistaCreatoreOut, status_code=201)
async def crea_call(
    data: CallCreaIn,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> CallVistaCreatoreOut:
    """Crea la bozza della call sul bando (titolare)."""
    return await partner_call_service.crea_bozza(primary, secondary, active, user, data)


@router.get("/{call_id}", response_model=CallVistaCreatoreOut)
async def get_call(
    call_id: str,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> CallVistaCreatoreOut:
    """La call per l'azienda creatrice (poll-on-read dei job AI e controllo
    delle chiusure automatiche)."""
    return await partner_call_service.dettaglio(primary, secondary, active, user, call_id)


@router.patch("/{call_id}", response_model=CallVistaCreatoreOut)
async def aggiorna_call(
    call_id: str,
    data: CallAggiornaIn,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> CallVistaCreatoreOut:
    """Aggiornamento parziale (dopo la pubblicazione solo la whitelist, con
    una nuova versione)."""
    return await partner_call_service.aggiorna(primary, secondary, active, user, call_id, data)


@router.post("/{call_id}/regole", response_model=CallVistaCreatoreOut)
async def conferma_regole(
    call_id: str,
    data: RegoleConfermaIn,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> CallVistaCreatoreOut:
    """Conferma le regole del bando e l'esclusività (solo in bozza)."""
    return await partner_call_service.conferma_regole(
        primary, secondary, active, user, call_id, data
    )


@router.post("/{call_id}/requisiti/genera", response_model=GapOut)
async def genera_requisiti(
    call_id: str,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> GapOut:
    """Proposta di requisiti con la copertura della tua azienda (non salvata)."""
    return await partner_call_service.genera_requisiti(primary, secondary, active, user, call_id)


@router.put("/{call_id}/requisiti", response_model=GapOut)
async def salva_requisiti(
    call_id: str,
    data: RequisitiIn,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> GapOut:
    """Salva i requisiti (sostituisce l'elenco)."""
    return await partner_call_service.salva_requisiti(
        primary, secondary, active, user, call_id, data
    )


@router.post("/{call_id}/posizioni/proposta", response_model=JobPosizioniOut, status_code=202)
async def proponi_posizioni(
    call_id: str,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
    ai: AiDep,
) -> JobPosizioniOut:
    """Avvia la proposta AI delle posizioni (costo della piattaforma, in
    background): 202, lo stato si legge dal dettaglio."""
    return await partner_call_service.avvia_proposta_posizioni(
        primary, secondary, ai, active, user, call_id
    )


@router.put("/{call_id}/posizioni", response_model=CallVistaCreatoreOut)
async def salva_posizioni(
    call_id: str,
    data: PosizioniIn,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> CallVistaCreatoreOut:
    """Salva le posizioni cercate (sostituisce l'elenco)."""
    return await partner_call_service.salva_posizioni(
        primary, secondary, active, user, call_id, data
    )


@router.post("/{call_id}/testi/proposta", response_model=JobTestiOut, status_code=202)
async def proponi_testi(
    call_id: str,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
    ai: AiDep,
) -> JobTestiOut:
    """Avvia la bozza AI dei testi pubblici: 202, lo stato si legge dal
    dettaglio."""
    return await partner_call_service.avvia_proposta_testi(
        primary, secondary, ai, active, user, call_id
    )


@router.get("/{call_id}/anteprima", response_model=AnteprimaOut)
async def anteprima_call(
    call_id: str,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> AnteprimaOut:
    """«Come ti vedono le altre aziende», con i rilievi sui testi pubblici."""
    return await partner_call_service.anteprima(primary, secondary, active, user, call_id)


@router.post("/{call_id}/pubblica", response_model=CallVistaCreatoreOut)
async def pubblica_call(
    call_id: str,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
    data: PubblicaIn | None = None,
) -> CallVistaCreatoreOut:
    """Pubblica la bozza (titolare): bando aperto, call completa, identità
    verificata e limiti del piano."""
    return await partner_call_service.pubblica(
        primary, secondary, active, user, call_id, data.scadenza_call if data else None
    )


@router.post("/{call_id}/chiudi", response_model=CallVistaCreatoreOut)
async def chiudi_call(
    call_id: str,
    data: ChiudiIn,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> CallVistaCreatoreOut:
    """Chiude la call: completata (partenariato fatto) o annullata."""
    return await partner_call_service.chiudi(primary, secondary, active, user, call_id, data)


@router.get("/{call_id}/versioni", response_model=list[VersioneOut])
async def versioni_call(
    call_id: str,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> list[VersioneOut]:
    """Le versioni pubblicate della call (azienda creatrice)."""
    return await partner_call_service.versioni(primary, secondary, active, user, call_id)

