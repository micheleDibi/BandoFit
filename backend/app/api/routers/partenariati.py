"""Rotte trasversali del modulo partenariati (flag sul router: 404 a flag
spento, anche senza token). WP3: il vocabolario controllato; WP4 aggiunge
l'informativa; WP5 le segnalazioni DSA; WP9 l'esito della segnalazione e il
ricorso (per chi ha segnalato e per l'azienda autrice del contenuto)."""

from fastapi import APIRouter, Depends

from app.api.deps import (
    ActiveCompanyDep,
    CurrentUser,
    PrimaryClient,
    RottaPartenariati,
    SecondaryClient,
    require_partenariati_attivo,
)
from app.schemas.partenariato_moderazione import RicorsoIn, SegnalazioneEsitoOut
from app.schemas.partenariato_vocabolario import VocabolarioOut
from app.schemas.partner_call import SegnalazioneIn, SegnalazioneOut
from app.schemas.partner_profile import InformativaPartnerOut
from app.services import partenariato_moderazione_service, partner_call_service
from app.services.partenariato_informativa import informativa_out
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


@router.get("/informativa", response_model=InformativaPartnerOut)
async def get_informativa(user: CurrentUser) -> InformativaPartnerOut:
    """Informativa per il profilo partner e per il referente, con le versioni
    da rimandare al consenso."""
    return informativa_out()


@router.post("/segnalazioni", response_model=SegnalazioneOut, status_code=201)
async def segnala(
    data: SegnalazioneIn,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> SegnalazioneOut:
    """Segnala una call o un profilo partner che vedi (DSA): 201 con la
    conferma di ricezione; 404 se il contenuto non è visibile, 409 se l'hai
    già segnalato, 429 oltre il limite giornaliero."""
    return await partner_call_service.segnala(primary, secondary, active, user, data)


@router.get("/segnalazioni/{segnalazione_id}", response_model=SegnalazioneEsitoOut)
async def get_segnalazione(
    segnalazione_id: str,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
) -> SegnalazioneEsitoOut:
    """Stato, decisione motivata ed eventuale ricorso di una segnalazione:
    per chi l'ha fatta, o per l'azienda attiva autrice del contenuto se la
    decisione l'ha riguardata (con lo statement of reasons). Mai l'identità
    dell'altra parte. 404 per chiunque altro."""
    return await partenariato_moderazione_service.dettaglio(primary, active, user,
                                                            segnalazione_id)


@router.post("/segnalazioni/{segnalazione_id}/ricorso", response_model=SegnalazioneEsitoOut)
async def presenta_ricorso(
    segnalazione_id: str,
    data: RicorsoIn,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
) -> SegnalazioneEsitoOut:
    """Ricorso interno (DSA art. 20), uno solo entro 6 mesi dalla decisione:
    il titolare dell'azienda autrice contro una restrizione, chi ha
    segnalato contro «nessuna azione». 400 `ricorso_testo_non_valido`
    (20..2000 caratteri), 403 per un membro dell'azienda autrice, 404, 409
    `ricorso_non_ammesso`."""
    return await partenariato_moderazione_service.ricorso(primary, active, user,
                                                          segnalazione_id, data)
