"""Operatività admin del modulo partenariati. WP3: estrazioni delle regole
per bando, forzatura e run manuale dello scheduler. WP9: coda delle
segnalazioni DSA (presa in carico, anteprima dello statement of reasons,
decisione motivata, ricorso, contesto della conversazione), elenco delle
call, sospensione e ripristino diretti, metriche e costi per valuta, coda
della verifica dell'identità con decisione e revoca. Flag sul router e sulla
route class (404 a flag spento, anche senza token e con un corpo
malformato); ogni rotta richiede un admin (AdminUser)."""

from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import (
    AdminUser,
    AiDep,
    PrimaryClient,
    SecondaryClient,
    RottaPartenariati,
    require_partenariati_attivo,
)
from app.core.config import get_settings
from app.core.errors import ConflictError
from app.schemas.common import Page
from app.schemas.partenariato import (
    EstrazioneAdminOut,
    ForzaEstrazioneIn,
    PartenariatiRunOut,
    PartenariatoBandoOut,
)
from app.schemas.partenariato_admin import (
    CallAdminOut,
    CostiOut,
    IdentitaAdminOut,
    IdentitaDecisioneIn,
    IdentitaEsitoOut,
    IdentitaRevocaIn,
    MetricheOut,
    StatoIdentita,
)
from app.schemas.partenariato_moderazione import (
    ContestoCompletoIn,
    ContestoOut,
    DecisioneIn,
    FiltroCoda,
    RicorsoDecisioneIn,
    SegnalazioneAdminOut,
    SospendiIn,
    SospensioneIn,
    SospensioneOut,
    StatementOut,
)
from app.schemas.partner_call import OggettoSegnalazione
from app.services import (
    partenariati_scheduler,
    partenariato_admin_service,
    partenariato_moderazione_service,
    partenariato_service,
)

router = APIRouter(
    prefix="/admin/partenariati",
    tags=["admin"],
    dependencies=[Depends(require_partenariati_attivo)],
    route_class=RottaPartenariati,
)

ADMIN_SELECT = (
    "bando_id,bando_slug,bando_titolo,stato,esito,fase,modalita_effettiva,model,cost_cents,"
    "input_tokens,output_tokens,estratta_at,verificata_at,ultima_esecuzione_at,errore_codice,"
    "tentativi_falliti,prossimo_tentativo_at,updated_at"
)


def _testo(valore) -> str | None:
    return str(valore) if valore is not None else None


@router.get("/estrazioni", response_model=Page[EstrazioneAdminOut])
async def list_estrazioni(
    user: AdminUser,
    primary: PrimaryClient,
    stato: Literal["in_corso", "pronta", "errore"] | None = None,
    esito: Literal["estratta", "nessun_segnale"] | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> Page[EstrazioneAdminOut]:
    """Estrazioni per bando, dalla più recente (mai claim né output grezzo)."""
    query = primary.table("bando_partenariato").select(ADMIN_SELECT, count="exact")
    if stato:
        query = query.eq("stato", stato)
    if esito:
        query = query.eq("esito", esito)
    offset = (page - 1) * page_size
    resp = (
        await query.order("updated_at", desc=True)
        .range(offset, offset + page_size - 1)
        .execute()
    )
    items = [
        EstrazioneAdminOut(
            **{
                **riga,
                **{
                    campo: _testo(riga.get(campo))
                    for campo in (
                        "estratta_at", "verificata_at", "ultima_esecuzione_at",
                        "prossimo_tentativo_at", "updated_at",
                    )
                },
            }
        )
        for riga in resp.data or []
    ]
    return Page.build(items, resp.count or 0, page, page_size)


@router.post("/estrazioni/{bando_id}", response_model=PartenariatoBandoOut)
async def forza_estrazione(
    bando_id: int,
    response: Response,
    user: AdminUser,
    primary: PrimaryClient,
    secondary: SecondaryClient,
    ai: AiDep,
    payload: ForzaEstrazioneIn | None = None,
) -> PartenariatoBandoOut:
    """Nuova estrazione del bando (paga sempre il modello, stesso budget
    giornaliero). 202 se partita, 200 se già in corso."""
    stato, avviata = await partenariato_service.forza_admin(
        primary,
        secondary,
        ai,
        user,
        bando_id,
        ignora_cooldown=bool(payload and payload.ignora_cooldown),
    )
    response.status_code = 202 if avviata else 200
    return stato


@router.post("/run", response_model=PartenariatiRunOut)
async def run_scheduler(
    user: AdminUser,
    primary: PrimaryClient,
    secondary: SecondaryClient,
    ai: AiDep,
    ripeti: bool = False,
) -> PartenariatiRunOut:
    """Esegue subito la run di oggi. Senza `ripeti` rispetta il claim (409 se
    già eseguita); con `ripeti=true` riesegue (i passi sono idempotenti e la
    spesa resta nel budget del giorno)."""
    oggi = datetime.now(ZoneInfo(get_settings().alert_fuso)).date()
    if not ripeti and not await partenariati_scheduler.claim_run(primary, oggi):
        raise ConflictError("La run di oggi è già stata eseguita (usa ripeti=true)")
    riepilogo = await partenariati_scheduler.esegui_run(primary, secondary, ai, oggi)
    return PartenariatiRunOut(giorno=oggi.isoformat(), riepilogo=riepilogo)


# ------------------------------------------------------------ WP9: moderazione

StatoCall = Literal[
    "bozza", "pubblicata", "scaduta", "chiusa_completata", "chiusa_annullata",
    "sospesa_moderazione",
]


@router.get("/segnalazioni", response_model=Page[SegnalazioneAdminOut])
async def coda_segnalazioni(
    user: AdminUser,
    primary: PrimaryClient,
    stato: FiltroCoda = "aperte",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> Page[SegnalazioneAdminOut]:
    """Coda delle segnalazioni: `aperte` (ricevute, in esame, con un ricorso
    da decidere) dalla più vecchia, oppure uno stato o `tutte`."""
    return await partenariato_moderazione_service.coda(primary, stato=stato, page=page,
                                                       page_size=page_size)


@router.get("/segnalazioni/{segnalazione_id}", response_model=SegnalazioneAdminOut)
async def get_segnalazione(
    segnalazione_id: str, user: AdminUser, primary: PrimaryClient,
) -> SegnalazioneAdminOut:
    return await partenariato_moderazione_service.dettaglio_admin(primary, segnalazione_id)


@router.post("/segnalazioni/{segnalazione_id}/prendi", response_model=SegnalazioneAdminOut)
async def prendi_segnalazione(
    segnalazione_id: str, user: AdminUser, primary: PrimaryClient,
) -> SegnalazioneAdminOut:
    """Presa in carico (`ricevuta → in_esame`). 409 `segnalazione_gia_decisa`."""
    return await partenariato_moderazione_service.prendi_in_carico(primary, user,
                                                                   segnalazione_id)


@router.post("/segnalazioni/{segnalazione_id}/anteprima", response_model=StatementOut)
async def anteprima_statement(
    segnalazione_id: str, data: DecisioneIn, user: AdminUser, primary: PrimaryClient,
) -> StatementOut:
    """Lo statement of reasons che riceverebbe l'autore con questa decisione
    (nessuna scrittura; `testo` null con `nessuna_azione`)."""
    return await partenariato_moderazione_service.anteprima_statement(primary, segnalazione_id,
                                                                      data)


@router.post("/segnalazioni/{segnalazione_id}/decidi", response_model=SegnalazioneAdminOut)
async def decidi_segnalazione(
    segnalazione_id: str, data: DecisioneIn, user: AdminUser, primary: PrimaryClient,
) -> SegnalazioneAdminOut:
    """Decisione motivata con effetto atomico (call sospesa, messaggio
    oscurato, profilo sospeso o nessuna azione). 400 `decisione_non_valida`,
    `motivazione_non_valida`; 404; 409 `segnalazione_gia_decisa`,
    `oggetto_non_sospendibile`."""
    return await partenariato_moderazione_service.decidi(primary, user, segnalazione_id, data)


@router.post("/segnalazioni/{segnalazione_id}/ricorso/decidi",
             response_model=SegnalazioneAdminOut)
async def decidi_ricorso(
    segnalazione_id: str, data: RicorsoDecisioneIn, user: AdminUser, primary: PrimaryClient,
) -> SegnalazioneAdminOut:
    """Decisione motivata del ricorso (`confermata` | `riformata`). 409
    `ricorso_non_in_attesa`."""
    return await partenariato_moderazione_service.decidi_ricorso(primary, user,
                                                                 segnalazione_id, data)


@router.get("/segnalazioni/{segnalazione_id}/contesto", response_model=ContestoOut)
async def contesto_segnalazione(
    segnalazione_id: str, user: AdminUser, primary: PrimaryClient,
) -> ContestoOut:
    """Messaggi attorno a quello segnalato (±10), con l'accesso registrato
    in audit prima dei dati (se non si scrive, 502 e nessun messaggio)."""
    return await partenariato_moderazione_service.contesto(primary, user, segnalazione_id)


@router.post("/segnalazioni/{segnalazione_id}/contesto", response_model=ContestoOut)
async def contesto_completo_segnalazione(
    segnalazione_id: str, data: ContestoCompletoIn, user: AdminUser, primary: PrimaryClient,
) -> ContestoOut:
    """La conversazione intera, solo con una `motivazione` (20..2000) nel
    corpo: la motivazione descrive il caso, quindi non viaggia nell'URL (che
    finisce nei log di accesso) ma nell'audit, scritto prima dei dati (se non
    si scrive, 502 e nessun messaggio)."""
    return await partenariato_moderazione_service.contesto(
        primary, user, segnalazione_id, completo=True, motivazione=data.motivazione)


@router.get("/call", response_model=Page[CallAdminOut])
async def lista_call(
    user: AdminUser,
    primary: PrimaryClient,
    stato: StatoCall | None = None,
    q: str | None = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> Page[CallAdminOut]:
    """Le call di tutte le aziende, dalla più recente (filtri: stato; testo
    nel titolo della call o del bando, o l'id della call)."""
    return await partenariato_admin_service.lista_call(primary, stato=stato, q=q, page=page,
                                                       page_size=page_size)


@router.post("/{oggetto_tipo}/{oggetto_id}/sospendi", response_model=SospensioneOut)
async def sospendi(
    oggetto_tipo: OggettoSegnalazione,
    oggetto_id: str,
    data: SospendiIn,
    user: AdminUser,
    primary: PrimaryClient,
) -> SospensioneOut:
    """Sospensione diretta, senza segnalazione: call (id; da bozza o
    pubblicata), profilo (codice pubblico), messaggio (id); motivazione
    20..500. L'autore riceve lo statement of reasons (in-app in forma breve,
    per email completo). 404; 409 `oggetto_non_sospendibile`."""
    return await partenariato_moderazione_service.sospendi(primary, user, oggetto_tipo,
                                                           oggetto_id, data)


@router.post("/{oggetto_tipo}/{oggetto_id}/ripristina", response_model=SospensioneOut)
async def ripristina(
    oggetto_tipo: OggettoSegnalazione,
    oggetto_id: str,
    data: SospensioneIn,
    user: AdminUser,
    primary: PrimaryClient,
) -> SospensioneOut:
    """Ripristino diretto: call allo stato precedente (o `scaduta` se nel
    frattempo è passata la scadenza), profilo riattivato, messaggio
    visibile. Già attivo: `modificato` false."""
    return await partenariato_moderazione_service.ripristina(primary, user, oggetto_tipo,
                                                             oggetto_id, data)


@router.get("/metriche", response_model=MetricheOut)
async def metriche(
    user: AdminUser, primary: PrimaryClient, da: date | None = None, a: date | None = None,
) -> MetricheOut:
    """Metriche del modulo sulle call pubblicate nel periodo (default: ultimi
    30 giorni). 400 `periodo_non_valido`."""
    return await partenariato_admin_service.metriche(primary, da, a)


@router.get("/costi", response_model=CostiOut)
async def costi(
    user: AdminUser, primary: PrimaryClient, da: date | None = None, a: date | None = None,
) -> CostiOut:
    """Costi del modulo per provider, servizio, esito e valuta (EUR openapi,
    USD Anthropic: mai sommati). 400 `periodo_non_valido`."""
    return await partenariato_admin_service.costi(primary, da, a)


# -------------------------------------------------- WP9: identità verificata


@router.get("/identita", response_model=Page[IdentitaAdminOut])
async def coda_identita(
    user: AdminUser,
    primary: PrimaryClient,
    stato: StatoIdentita | Literal["tutte"] = "richiesta",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> Page[IdentitaAdminOut]:
    """Richieste di verifica dell'identità in attesa (o le aziende in un altro
    stato), con i recapiti della sede dal Registro Imprese."""
    return await partenariato_admin_service.coda_identita(primary, stato=stato, page=page,
                                                          page_size=page_size)


@router.post("/identita/{company_id}/decidi", response_model=IdentitaEsitoOut)
async def decidi_identita(
    company_id: str, data: IdentitaDecisioneIn, user: AdminUser, primary: PrimaryClient,
) -> IdentitaEsitoOut:
    """Verifica (metodo obbligatorio) o rifiuto di una richiesta in attesa;
    notifica al titolare. 400 `metodo_obbligatorio`; 404; 409
    `identita_non_richiesta`, `identita_non_verificata` (registro non
    coerente)."""
    return await partenariato_admin_service.decidi_identita(primary, user, company_id, data)


@router.post("/identita/{company_id}/revoca", response_model=IdentitaEsitoOut)
async def revoca_identita(
    company_id: str, data: IdentitaRevocaIn, user: AdminUser, primary: PrimaryClient,
) -> IdentitaEsitoOut:
    """Revoca di una verifica (motivo obbligatorio, ≤ 500); notifica al
    titolare. Non verificata: `modificato` false."""
    return await partenariato_admin_service.revoca_identita(primary, user, company_id, data)
