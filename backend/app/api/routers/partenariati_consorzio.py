"""Consorzio della call di partenariato (WP8, docs/partenariati.md V1-V3, §5).

Router NUOVO del modulo: flag sul router e sulla route class (404 anche
senza token e con un corpo malformato, T1). Il consorzio lo vedono l'azienda
che ha creato la call (titolare e membri con visibilità, in lettura) e le
controparti accettate; per chiunque altro, anche un'altra azienda dello
stesso owner, 404. Scrive solo il titolare dell'azienda attiva: il creatore
gestisce membri, esterni, budget e documenti; ogni azienda conferma la
propria riga ed esce da sé. Ogni risposta è il consorzio aggiornato come lo
vede chi chiama (mai `company_profile_id` di altri né valori esatti di
bilancio di altri membri).
"""

from fastapi import APIRouter, Depends

from app.api.deps import (
    ActiveCompanyDep,
    CurrentUser,
    PrimaryClient,
    RottaPartenariati,
    SecondaryClient,
    require_partenariati_attivo,
)
from app.schemas.partenariato_consorzio import (
    BudgetIn,
    ConsorzioOut,
    DocumentoStatoIn,
    EsternoIn,
    MembroAggiornaIn,
    MembroConfermaIn,
)
from app.services import partenariato_consorzio_service as consorzio

router = APIRouter(
    prefix="/partenariati/call",
    tags=["partenariati"],
    dependencies=[Depends(require_partenariati_attivo)],
    route_class=RottaPartenariati,
)


@router.get("/{call_id}/consorzio", response_model=ConsorzioOut)
async def get_consorzio(
    call_id: str,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> ConsorzioOut:
    """Membri, validatore (voci verde / rosso / grigio con la regola di
    origine), matrice di copertura, checklist dei documenti e budget, come li
    vede l'azienda attiva."""
    return await consorzio.get_consorzio(primary, secondary, active, user, call_id)


@router.put("/{call_id}/consorzio/membri/{membro_id}", response_model=ConsorzioOut)
async def aggiorna_membro(
    call_id: str,
    membro_id: str,
    data: MembroAggiornaIn,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> ConsorzioOut:
    """Ruolo, posizione e quota di un membro (solo il creatore): un membro
    diverso dal creatore torna da confermare. 409 `call_non_modificabile`,
    `membro_uscito`, `capofila_gia_presente`, `ruolo_non_ammesso`."""
    return await consorzio.aggiorna_membro(primary, secondary, active, user, call_id,
                                           membro_id, data)


@router.post("/{call_id}/consorzio/membri/{membro_id}/conferma", response_model=ConsorzioOut)
async def conferma(
    call_id: str,
    membro_id: str,
    data: MembroConfermaIn,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> ConsorzioOut:
    """Conferma della propria partecipazione (il creatore anche per gli
    esterni) sui termini visti (body: ruolo, posizione e quota mostrati).
    409 `membro_modificato` (cambiati nel frattempo: rileggere),
    `quota_mancante`, `membro_uscito`, `call_non_modificabile`."""
    return await consorzio.conferma(primary, secondary, active, user, call_id, membro_id,
                                    data)


@router.post("/{call_id}/consorzio/membri/{membro_id}/esci", response_model=ConsorzioOut)
async def esci(
    call_id: str,
    membro_id: str,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> ConsorzioOut:
    """Uscita dal consorzio: il creatore toglie un membro, un'azienda esce da
    sé, anche da una call sospesa per moderazione (WP9: solo la propria riga;
    la risposta ha solo quella). 409 `membro_non_rimovibile` (il creatore
    resta), `call_non_modificabile`."""
    return await consorzio.esci(primary, secondary, active, user, call_id, membro_id)


@router.post("/{call_id}/consorzio/esterni", response_model=ConsorzioOut, status_code=201)
async def aggiungi_esterno(
    call_id: str,
    data: EsternoIn,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> ConsorzioOut:
    """Membro esterno (non in piattaforma: nome, paese, tipi di soggetto e
    quota dichiarati dal creatore). 400 `testo_non_conforme`; 409
    `limite_membri`, `capofila_gia_presente`, `call_non_modificabile`."""
    return await consorzio.aggiungi_o_modifica_esterno(primary, secondary, active, user,
                                                       call_id, data)


@router.put("/{call_id}/consorzio/esterni/{membro_id}", response_model=ConsorzioOut)
async def modifica_esterno(
    call_id: str,
    membro_id: str,
    data: EsternoIn,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> ConsorzioOut:
    """Modifica di un membro esterno (anche uscito: torna da confermare)."""
    return await consorzio.aggiungi_o_modifica_esterno(primary, secondary, active, user,
                                                       call_id, data, membro_id)


@router.put("/{call_id}/consorzio/budget", response_model=ConsorzioOut)
async def aggiorna_budget(
    call_id: str,
    data: BudgetIn,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> ConsorzioOut:
    """Fascia pubblica e budget esatto riservato (solo il creatore, call
    pubblicata). 400 budget fuori fascia; 409 `stato_call_non_valido`."""
    return await consorzio.aggiorna_budget(primary, secondary, active, user, call_id, data)


@router.put("/{call_id}/consorzio/documenti/{codice}", response_model=ConsorzioOut)
async def set_documento(
    call_id: str,
    codice: str,
    data: DocumentoStatoIn,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> ConsorzioOut:
    """Stato e note di un documento della checklist (solo il creatore). 400
    `documento_non_valido`, `testo_non_conforme`; 409
    `call_non_modificabile`."""
    return await consorzio.set_documento(primary, secondary, active, user, call_id, codice,
                                         data)
