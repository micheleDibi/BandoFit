from fastapi import APIRouter, Depends, Response

from app.api.deps import (
    ActiveCompanyDep,
    CurrentUser,
    OpenapiDep,
    PrimaryClient,
    SecondaryClient,
    require_bilanci_storico_attivo,
)
from app.schemas.bilanci import BilanciOut
from app.schemas.bilancio_ufficiale import (
    BilanciUfficialiOut,
    BilancioRichiestaIn,
    BilancioRichiestaOut,
)
from app.schemas.company import CompanyFacetsOut, CompanyIn, CompanyResponse
from app.schemas.openapi_data import (
    DossierResponse,
    ImportConfirmIn,
    ImportIn,
    ImportPreview,
    ImportResult,
)
from app.services import (
    bilanci_service,
    bilancio_ufficiale_service,
    company_pdf_service,
    company_service,
    compatibility,
    lookup_service,
    openapi_service,
)


def _pdf_response(result) -> Response:
    """Risposta binaria di download per un PdfResult (filename ASCII slugificato)."""
    return Response(
        content=result.content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{result.filename}"'},
    )

router = APIRouter(prefix="/me/company", tags=["company"])


@router.get("", response_model=CompanyResponse)
async def get_company(active: ActiveCompanyDep, primary: PrimaryClient) -> CompanyResponse:
    """Dati dell'azienda attiva: propri per il titolare, della famiglia (sola
    lettura) per un figlio attivo."""
    return await company_service.get_company(primary, active)


@router.put("", response_model=CompanyResponse)
async def save_company(
    data: CompanyIn,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> CompanyResponse:
    """Scrittura sull'azienda attiva: bloccata SOLO per i figli attivi (che
    ereditano i dati della famiglia); pending e retrocessi sono account
    indipendenti con dati propri. Se l'owner non ha ancora un'azienda, questo
    è il bootstrap della prima."""
    return await company_service.upsert_company(primary, secondary, active, data)


@router.get("/facets", response_model=CompanyFacetsOut)
async def company_facets(
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> CompanyFacetsOut:
    """Cosa l'azienda è DAVVERO, per i filtri: tutte le sedi, non la sola sede
    legale, e le divisioni ATECO secondarie oltre alla principale. Stessa
    funzione che alimenta il badge di compatibilità e l'AI-check.

    Un figlio attivo vede i facet della famiglia, come per i dati aziendali."""
    lookups = await lookup_service.get_lookups(secondary)
    facets = await compatibility.load_company_facets(primary, active, lookups)
    if facets is None:
        return CompanyFacetsOut()
    return CompanyFacetsOut(
        regioni=sorted(facets.regioni_ids),
        ateco=sorted(facets.ateco_ids),
        settori=[facets.settore_id] if facets.settore_id is not None else [],
        beneficiari=sorted(facets.beneficiari_ids),
        sufficiente=facets.sufficiente,
    )


@router.post("/import/preview", response_model=ImportPreview)
async def preview_import(
    data: ImportIn,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
    openapi: OpenapiDep,
) -> ImportPreview:
    """Recupera IT-full (e, se serve, lo storico dei bilanci IT-advanced) da
    openapi.it (A PAGAMENTO) per l'azienda attiva e mostra cosa si sta per
    importare. NON scrive nulla: i payload restano in staging fino alla
    conferma. Protetto da P.IVA legata all'azienda, cooldown, lock e tetto
    giornaliero; riusa gratis un'anteprima già pagata per la stessa azienda."""
    return await openapi_service.preview_import(
        primary, secondary, openapi, active, data.partita_iva
    )


@router.post("/import/confirm", response_model=ImportResult, status_code=201)
async def confirm_import(
    data: ImportConfirmIn,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> ImportResult:
    """Scrive i dati dell'anteprima sull'azienda attiva e compila i campi
    aziendali vuoti. Nessuna chiamata al provider: gratis, e fuori dal cooldown."""
    return await openapi_service.confirm_import(primary, secondary, active, data.partita_iva)


@router.get("/dossier", response_model=DossierResponse)
async def get_dossier(active: ActiveCompanyDep, primary: PrimaryClient) -> DossierResponse:
    """Dossier certificato importato da openapi.it: proprio per il titolare,
    in sola lettura per un figlio attivo."""
    return await openapi_service.get_dossier(primary, active)


@router.get("/bilanci", response_model=BilanciOut)
async def get_bilanci(active: ActiveCompanyDep, primary: PrimaryClient) -> BilanciOut:
    """Bilanci per esercizio dell'azienda attiva (riga fusa per anno, fonte di
    ogni campo, indicatori e fasce): titolare e membri con visibilità, questi
    ultimi in sola lettura. Gratis: al più rimappa i payload già pagati."""
    return await bilanci_service.get_bilanci(primary, active)


@router.post(
    "/bilanci/recupera",
    response_model=BilanciOut,
    dependencies=[Depends(require_bilanci_storico_attivo)],
)
async def recupera_bilanci(
    active: ActiveCompanyDep, primary: PrimaryClient, openapi: OpenapiDep
) -> BilanciOut:
    """«Recupera i bilanci»: storico IT-advanced (A PAGAMENTO) per l'azienda
    attiva. Solo il titolare; stesse guardie dell'import (P.IVA valida, niente
    società di persone, cooldown, lock, tetto giornaliero)."""
    return await bilanci_service.recupera_bilanci(primary, openapi, active)


@router.get(
    "/bilanci/ufficiale",
    response_model=BilanciUfficialiOut,
    dependencies=[Depends(require_bilanci_storico_attivo)],
)
async def lista_bilanci_ufficiali(
    active: ActiveCompanyDep, primary: PrimaryClient, openapi: OpenapiDep
) -> BilanciUfficialiOut:
    """Bilanci ufficiali dell'azienda attiva: addon, unità del titolare, anni
    già acquisiti e ultime richieste. Le richieste aperte avanzano in
    background (la lettura non aspetta il provider)."""
    return await bilancio_ufficiale_service.lista(primary, openapi, active)


@router.post(
    "/bilanci/ufficiale",
    response_model=BilancioRichiestaOut,
    status_code=201,
    dependencies=[Depends(require_bilanci_storico_attivo)],
)
async def richiedi_bilancio_ufficiale(
    data: BilancioRichiestaIn,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    openapi: OpenapiDep,
    user: CurrentUser,
) -> BilancioRichiestaOut:
    """Richiede il bilancio ufficiale (bilancio ottico, A PAGAMENTO) per
    l'azienda attiva: consuma 1 unità dell'addon «bilancio-ufficiale» del
    titolare, restituita in automatico se il Registro Imprese non lo
    fornisce. Solo il titolare."""
    return await bilancio_ufficiale_service.richiedi(primary, openapi, active, user, data.anno)


@router.get(
    "/bilanci/ufficiale/{richiesta_id}",
    response_model=BilancioRichiestaOut,
    dependencies=[Depends(require_bilanci_storico_attivo)],
)
async def dettaglio_bilancio_ufficiale(
    richiesta_id: str, active: ActiveCompanyDep, primary: PrimaryClient, openapi: OpenapiDep
) -> BilancioRichiestaOut:
    """Una richiesta di bilancio ufficiale dell'azienda attiva (404 altrimenti)."""
    return await bilancio_ufficiale_service.dettaglio(primary, openapi, active, richiesta_id)


@router.get(
    "/bilanci/ufficiale/{richiesta_id}/pdf",
    dependencies=[Depends(require_bilanci_storico_attivo)],
)
async def scarica_bilancio_ufficiale(
    richiesta_id: str, active: ActiveCompanyDep, primary: PrimaryClient, user: CurrentUser
) -> Response:
    """PDF del bilancio ufficiale, con autorizzazione live sull'azienda attiva:
    404 fuori azienda, 409 se il PDF non è conservato. Nome generato dal
    server; niente sniffing del tipo."""
    contenuto, nome = await bilancio_ufficiale_service.scarica_pdf(
        primary, active, richiesta_id, user=user
    )
    return Response(
        content=contenuto,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{nome}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/export/pdf")
async def export_scheda_pdf(
    active: ActiveCompanyDep, primary: PrimaryClient, user: CurrentUser
) -> Response:
    """PDF della scheda azienda attiva: dati dichiarati + preferenze seguite.
    Titolare o figlio attivo (sola lettura); 404 se non c'è ancora un'azienda."""
    result = await company_pdf_service.export_scheda_pdf(primary, user, active)
    return _pdf_response(result)


@router.get("/dossier/pdf")
async def export_dossier_pdf(active: ActiveCompanyDep, primary: PrimaryClient) -> Response:
    """PDF del dossier certificato dell'azienda attiva. Il payload grezzo del
    provider non esce mai (si parte da `get_dossier`, già ripulito). 404 se
    l'azienda non ha un dossier importato."""
    result = await company_pdf_service.export_dossier_pdf(primary, active)
    return _pdf_response(result)
