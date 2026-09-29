"""Bozze AI dei documenti del partenariato (WP10, docs/partenariati.md W4, §5).

Router NUOVO del modulo: flag sul router e sulla route class (404 anche
senza token e con un corpo malformato, T1). Le bozze (lettera d'intenti, NDA,
term sheet) le avvia il titolare dell'azienda attiva che partecipa alla call
(creatrice o membro non uscito del consorzio) e le leggono anche i membri con
visibilità; ogni azienda vede solo le proprie, e per chiunque altro la call è
404. Generazione come job asincrono: 202 e poll sul GET (T7). Ogni risposta
porta il disclaimer fisso; il PDF ha il nome generato dal server.
"""

from fastapi import APIRouter, Depends, Response

from app.api.deps import (
    ActiveCompanyDep,
    AiDep,
    CurrentUser,
    PrimaryClient,
    RottaPartenariati,
    SecondaryClient,
    require_partenariati_attivo,
)
from app.schemas.partenariato_bozze import BozzaAvviaIn, BozzaOut, BozzeOut
from app.services import partenariato_bozze_service as bozze

router = APIRouter(
    prefix="/partenariati/call",
    tags=["partenariati"],
    dependencies=[Depends(require_partenariati_attivo)],
    route_class=RottaPartenariati,
)


@router.post("/{call_id}/bozze", response_model=BozzaOut, status_code=202)
async def avvia_bozza(
    call_id: str,
    data: BozzaAvviaIn,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
    ai: AiDep,
) -> BozzaOut:
    """Avvia la bozza di un documento (poll sul GET). 403 `forbidden` (non
    titolare), 404, 409 `funzione_non_inclusa` / `bozze_esaurite` /
    `bozza_in_corso`, 429 `ai_sospesa_oggi`, 503 `ai_not_configured`."""
    return await bozze.avvia(primary, secondary, ai, active, user, call_id, data.tipo,
                             includi_nome_azienda=data.includi_nome_azienda)


@router.get("/{call_id}/bozze", response_model=BozzeOut)
async def lista_bozze(
    call_id: str,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
) -> BozzeOut:
    """Le bozze dell'azienda attiva sulla call, dalla più recente."""
    return await bozze.lista(primary, active, user, call_id)


@router.get("/{call_id}/bozze/{bozza_id}", response_model=BozzaOut)
async def dettaglio_bozza(
    call_id: str,
    bozza_id: str,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
) -> BozzaOut:
    """Una bozza dell'azienda attiva (stato del job, contenuto quando è pronta)."""
    return await bozze.stato(primary, active, user, call_id, bozza_id)


@router.get("/{call_id}/bozze/{bozza_id}/pdf")
async def scarica_bozza_pdf(
    call_id: str,
    bozza_id: str,
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
) -> Response:
    """PDF della bozza pronta, con il disclaimer in testa e a piè di pagina.
    409 `bozza_non_pronta`, 503 `pdf_unavailable`."""
    contenuto, nome = await bozze.pdf(primary, active, user, call_id, bozza_id)
    return Response(
        content=contenuto,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{nome}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
