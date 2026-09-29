"""Call di partenariato (WP5, docs/partenariati.md §2.5 e §5).

Router NUOVO del modulo partenariati: il flag sta sul router e sulla route
class, quindi a flag spento ogni rotta risponde 404 anche senza token e con un
corpo malformato (T1). Scrive solo il titolare dell'azienda attiva; i membri
con visibilità leggono; tutto ciò che non è dell'azienda attiva è 404 (T3).
Le proposte AI sono job asincroni: 202 e poll sul dettaglio (T7).

WP6: la lista ha le viste `tutte` (bacheca delle call di altri owner, con
filtri, ordinamento e il proprio match) e `salvate`; il dettaglio di una call
di un'altra azienda è la vista pubblica con il proprio match; il creatore
vede i suggeriti (pseudonimi, mai id interni); «Salva» vale come «segui».
"""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
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
from app.services.partenariato_accesso import (
    CallBachecaOut,
    CallPubblicaDettaglioOut,
    CallVistaControparteOut,
    SuggeritiOut,
)
from app.services.partenariato_matching import MatchOut

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


@router.get("", response_model=Page[CallBachecaOut])
async def lista_call(
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
    vista: Literal["mie", "tutte", "salvate"] = Query("mie"),
    bando: str | None = Query(None, max_length=200),
    regione: int | None = Query(None, ge=1),
    forma: str | None = Query(None, max_length=40),
    ruolo: Literal["capofila", "partner"] | None = Query(None),
    ordine: Literal["affinita", "recenti", "scadenza"] = Query("affinita"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
) -> Page[CallBachecaOut]:
    """`mie`: le call dell'azienda attiva; `tutte`: la bacheca (call
    pubblicate e visibili a tutti di altri owner, con il match dell'azienda
    attiva); `salvate`: quelle seguite. Filtri e ordinamento valgono per
    `tutte` e `salvate`."""
    return await partner_call_service.bacheca(
        primary, secondary, active, user, vista=vista, bando=bando, regione=regione,
        forma=forma, ruolo=ruolo, ordine=ordine, page=page, page_size=page_size,
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


@router.get(
    "/{call_id}",
    response_model=CallVistaCreatoreOut | CallVistaControparteOut | CallPubblicaDettaglioOut,
)
async def get_call(
    call_id: str,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> CallVistaCreatoreOut | CallVistaControparteOut | CallPubblicaDettaglioOut:
    """La call per l'azienda creatrice (poll-on-read dei job AI e controllo
    delle chiusure automatiche); per la controparte accettata (WP7) la vista
    controparte (`vista: "controparte"`); per le altre aziende la vista
    pubblica con il proprio match (senza `editable`), i requisiti
    dichiarabili e la propria candidatura (in attesa anche su una call solo
    su invito; chiusa solo finché la call è visibile a tutti)."""
    return await partner_call_service.dettaglio(primary, secondary, active, user, call_id)


@router.get("/{call_id}/suggeriti", response_model=SuggeritiOut)
async def suggeriti_call(
    call_id: str,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
    page: int = Query(1, ge=1),
    posizione: str | None = Query(None, max_length=40),
) -> SuggeritiOut:
    """Aziende suggerite per la call (azienda creatrice): pseudonimi, match
    in vista «terzi», al massimo 2 aziende dello stesso owner per pagina."""
    return await partner_call_service.suggeriti(
        primary, secondary, active, user, call_id, page=page, posizione_id=posizione
    )


@router.get("/{call_id}/match", response_model=MatchOut | None)
async def match_call(
    call_id: str,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> MatchOut | None:
    """Il match dell'azienda attiva con la call (vista «proprio»); null per
    le proprie call o se non è compatibile."""
    return await partner_call_service.match_call(primary, secondary, active, user, call_id)


@router.post("/{call_id}/salva", status_code=204)
async def salva_call(
    call_id: str,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> Response:
    """Salva («segui») la call (titolare): notifiche su modifica e chiusura."""
    await partner_call_service.salva(primary, secondary, active, user, call_id)
    return Response(status_code=204)


@router.delete("/{call_id}/salva", status_code=204)
async def rimuovi_salvata(
    call_id: str,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> Response:
    """Smette di seguire la call (titolare)."""
    await partner_call_service.rimuovi_salvata(primary, secondary, active, user, call_id)
    return Response(status_code=204)


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

