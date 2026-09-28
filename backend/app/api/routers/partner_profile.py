"""Profilo partner dell'azienda attiva (WP4, docs/partenariati.md §2.4).

Router NUOVO del modulo partenariati: il flag sta sul router e sulla route
class, quindi a flag spento ogni rotta risponde 404 anche senza token e con un
corpo malformato (T1). Scrive solo il titolare; i membri con visibilità
leggono; la risposta alla proposta di referente è del membro proposto.
"""

from fastapi import APIRouter, Depends

from app.api.deps import (
    ActiveCompanyDep,
    AiDep,
    CurrentUser,
    PrimaryClient,
    RottaPartenariati,
    SecondaryClient,
    require_partenariati_attivo,
)
from app.schemas.partner_profile import (
    ConsensoIn,
    PartnerProfileIn,
    PartnerProfileOut,
    PartnerPubblicoOut,
    ReferenteIn,
    ReferenteRispostaIn,
)
from app.services import partner_profile_service

router = APIRouter(
    prefix="/me/partner-profile",
    tags=["partenariati"],
    dependencies=[Depends(require_partenariati_attivo)],
    route_class=RottaPartenariati,
)


@router.get("", response_model=PartnerProfileOut)
async def get_partner_profile(
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> PartnerProfileOut:
    """Profilo partner, consenso, identità dal registro, referente e bozza AI
    dell'azienda attiva (poll-on-read della bozza)."""
    return await partner_profile_service.get_profilo(primary, secondary, active, user)


@router.put("", response_model=PartnerProfileOut)
async def salva_partner_profile(
    data: PartnerProfileIn,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> PartnerProfileOut:
    """Salva i campi liberi del profilo (titolare). Nessun contatto nei testi;
    se il profilo è anonimo, nemmeno i dati che identificano l'azienda."""
    return await partner_profile_service.salva_profilo(primary, secondary, active, user, data)


@router.post("/consenso", response_model=PartnerProfileOut)
async def consenso_partner(
    data: ConsensoIn,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> PartnerProfileOut:
    """Concede o revoca la visibilità come partner, o cambia l'anonimato
    (titolare, con la versione dell'informativa letta)."""
    return await partner_profile_service.consenso(primary, secondary, active, user, data)


@router.post("/referente", response_model=PartnerProfileOut)
async def referente_partner(
    data: ReferenteIn,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> PartnerProfileOut:
    """Il titolare propone un membro come referente (che deve accettare) o
    lo rimuove."""
    return await partner_profile_service.referente(primary, secondary, active, user, data)


@router.post("/referente/risposta", response_model=PartnerProfileOut)
async def risposta_referente_partner(
    data: ReferenteRispostaIn,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> PartnerProfileOut:
    """Il membro proposto accetta o rifiuta; il referente può rinunciare."""
    return await partner_profile_service.risposta_referente(
        primary, secondary, active, user, data
    )


@router.post("/bozza-ai", response_model=PartnerProfileOut, status_code=202)
async def avvia_bozza_ai(
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
    ai: AiDep,
) -> PartnerProfileOut:
    """Avvia la bozza AI di descrizione e competenze (costo della
    piattaforma, in background): 202 con la bozza `in_corso`."""
    return await partner_profile_service.avvia_bozza_ai(primary, secondary, ai, active, user)


@router.delete("/bozza-ai", response_model=PartnerProfileOut)
async def scarta_bozza_ai(
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> PartnerProfileOut:
    """Scarta la proposta AI (titolare)."""
    return await partner_profile_service.scarta_bozza(primary, secondary, active, user)


@router.get("/anteprima", response_model=PartnerPubblicoOut)
async def anteprima_partner(
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> PartnerPubblicoOut:
    """«Come ti vedono le altre aziende»: la proiezione pubblica del profilo."""
    return await partner_profile_service.anteprima(primary, secondary, active, user)
