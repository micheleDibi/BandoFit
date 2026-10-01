"""Chat in-app delle call di partenariato (WP7, docs/partenariati.md K3-K4,
§5).

Router NUOVO del modulo: flag sul router e sulla route class (404 anche
senza token e con un corpo malformato, T1). Una conversazione esiste solo per
le sue due aziende (404 per chiunque altro, anche per un'altra azienda dello
stesso owner); scrive il titolare, chiude il titolare dell'azienda
creatrice, i membri con visibilità leggono e segnano come letti. Per
segnalare un messaggio: `POST /partenariati/segnalazioni` con
`oggetto_tipo='messaggio'`.
"""

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import (
    ActiveCompanyDep,
    CurrentUser,
    PrimaryClient,
    RottaPartenariati,
    SecondaryClient,
    require_partenariati_attivo,
)
from app.schemas.common import Page
from app.services import partenariato_chat_service as chat
from app.services.partenariato_chat_service import (
    ConversazioneOut,
    ConversazioneRigaOut,
    LettoIn,
    MessaggiOut,
    MessaggioIn,
    MessaggioOut,
)

router = APIRouter(
    prefix="/partenariati/conversazioni",
    tags=["partenariati"],
    dependencies=[Depends(require_partenariati_attivo)],
    route_class=RottaPartenariati,
)


@router.get("", response_model=Page[ConversazioneRigaOut])
async def lista(
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
    page: int = Query(1, ge=1, le=100_000),
    page_size: int = Query(20, ge=1, le=50),
) -> Page[ConversazioneRigaOut]:
    """Conversazioni dell'azienda attiva con i non letti dell'utente."""
    return await chat.lista_conversazioni(primary, secondary, active, user, page=page,
                                          page_size=page_size)


@router.get("/{conversazione_id}", response_model=ConversazioneOut)
async def dettaglio(
    conversazione_id: str,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> ConversazioneOut:
    return await chat.dettaglio(primary, secondary, active, user, conversazione_id)


@router.get("/{conversazione_id}/messaggi", response_model=MessaggiOut)
async def messaggi(
    conversazione_id: str,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
    dopo: int | None = Query(None, ge=0),
    prima: int | None = Query(None, ge=1),
    limite: int = Query(50, ge=1, le=100),
) -> MessaggiOut:
    """Messaggi in ordine crescente: `dopo` per il polling dei nuovi, `prima`
    per scorrere all'indietro, senza cursori gli ultimi `limite`."""
    return await chat.messaggi(primary, secondary, active, user, conversazione_id, dopo=dopo,
                               prima=prima, limite=limite)


@router.post("/{conversazione_id}/messaggi", response_model=MessaggioOut, status_code=201)
async def invia(
    conversazione_id: str,
    data: MessaggioIn,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> MessaggioOut:
    """Nuovo messaggio (idempotente per `client_msg_id`). 409
    `conversazione_chiusa`, `controparte_non_disponibile`; 429
    `limite_messaggi`."""
    return await chat.invia(primary, secondary, active, user, conversazione_id, data)


@router.post("/{conversazione_id}/letto", status_code=204)
async def letto(
    conversazione_id: str,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
    data: LettoIn | None = None,
) -> Response:
    """Segna come letti i messaggi fino a `fino_a_id` (assente: tutti)."""
    await chat.segna_letto(primary, secondary, active, user, conversazione_id,
                           data or LettoIn())
    return Response(status_code=204)


@router.post("/{conversazione_id}/chiudi", response_model=ConversazioneOut)
async def chiudi(
    conversazione_id: str,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> ConversazioneOut:
    """Chiusura da parte del titolare dell'azienda creatrice della call."""
    return await chat.chiudi(primary, secondary, active, user, conversazione_id)
