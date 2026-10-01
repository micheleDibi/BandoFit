from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import ActiveCompanyDep, PrimaryClient, SecondaryClient
from app.core.config import get_settings
from app.core.errors import BadRequestError
from app.schemas.bando import BandoDetail, BandoListItem, LookupsOut
from app.schemas.common import Page
from app.services import bandi_service, lookup_service
from app.services.bandi_service import DEFAULT_SORT, SORT_OPTIONS, BandiFilters
from app.services.compatibility import get_company_facets


async def get_lookups(secondary) -> LookupsOut:
    """Lookup per elenco e dettaglio: il router legge e mostra, quindi in
    degrado (catalogo non leggibile, nessuna cache) riceve liste vuote invece
    di un errore."""
    return await lookup_service.get_lookups(secondary, degrada=True)


async def _facets_per_il_badge(primary, active, lookups: LookupsOut):
    """Facet dell'azienda per il badge di compatibilità. Con le liste vuote
    del degrado nessun badge (None), senza leggere né mettere in cache i
    facet: perderebbero le divisioni ATECO secondarie e il punteggio
    uscirebbe sottostimato."""
    if lookup_service.vuoti_in_degrado(lookups):
        return None
    return await get_company_facets(primary, active, lookups)

router = APIRouter(prefix="/bandi", tags=["bandi"])

_VALID_STATI = {"aperto", "chiuso", "in apertura prossimamente", "sospeso", "revocato"}
_VALID_LIVELLI = {"flash_bando", "guida_bando"}


def _csv_ints(raw: str | None, param: str) -> list[int]:
    if not raw:
        return []
    try:
        return [int(x) for x in raw.split(",") if x.strip()]
    except ValueError as exc:
        raise BadRequestError(f"Parametro '{param}' non valido: attesi id numerici") from exc


def parse_filters(
    q: str | None = Query(default=None, max_length=200, description="Ricerca full-text"),
    stato: str | None = Query(
        default=None,
        description=(
            "Stati separati da virgola: aperto, in apertura prossimamente, chiuso, sospeso, "
            "revocato. In elenco: aperti, poi chiusi, poi sospesi e revocati"
        ),
    ),
    livello: str | None = Query(default=None),
    tipologie: str | None = Query(default=None, description="Id separati da virgola"),
    modalita: str | None = Query(default=None),
    programmi: str | None = Query(default=None),
    regioni: str | None = Query(default=None),
    settori: str | None = Query(default=None),
    beneficiari: str | None = Query(default=None),
    ateco: str | None = Query(default=None),
    importo_min: int | None = Query(default=None, ge=0),
    importo_max: int | None = Query(default=None, ge=0),
    scadenza_da: date | None = Query(default=None),
    scadenza_a: date | None = Query(default=None),
    scade_entro_giorni: int | None = Query(default=None, ge=1, le=365),
) -> BandiFilters:
    # Gli stati sconosciuti si ignorano, mai un 400: il catalogo può introdurre
    # stati nuovi prima di noi (contratto DB bandi §7, R0-a).
    stati = [s.strip() for s in (stato.split(",") if stato else [])]
    stati = [s for s in stati if s in _VALID_STATI]
    if livello and livello not in _VALID_LIVELLI:
        raise BadRequestError(f"Livello non valido: {livello}")
    return BandiFilters(
        q=q,
        stato=stati,
        livello=livello,
        tipologie=_csv_ints(tipologie, "tipologie"),
        modalita=_csv_ints(modalita, "modalita"),
        programmi=_csv_ints(programmi, "programmi"),
        regioni=_csv_ints(regioni, "regioni"),
        settori=_csv_ints(settori, "settori"),
        beneficiari=_csv_ints(beneficiari, "beneficiari"),
        codici_ateco=_csv_ints(ateco, "ateco"),
        importo_min=importo_min,
        importo_max=importo_max,
        scadenza_da=scadenza_da,
        scadenza_a=scadenza_a,
        scade_entro_giorni=scade_entro_giorni,
    )


@router.get("", response_model=Page[BandoListItem])
async def list_bandi(
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
    filters: Annotated[BandiFilters, Depends(parse_filters)],
    # Tetto alla pagina: oltre l'ultima l'elenco risponde vuoto, ma un offset
    # smisurato non deve arrivare al catalogo.
    page: int = Query(default=1, ge=1, le=100_000),
    page_size: int = Query(default=20, ge=1, le=50),
    sort: str = Query(default=DEFAULT_SORT),
    # Nessun vincolo di formato: un valore qualunque si ignora, mai un 422.
    partenariato: str | None = Query(
        default=None,
        description="ammesso | obbligatorio: solo tra i bandi con le regole già analizzate",
    ),
) -> Page[BandoListItem]:
    if sort not in SORT_OPTIONS:
        raise BadRequestError(f"Ordinamento non valido: {sort}")
    # Filtro del modulo partenariati: a flag spento (o con un valore ignoto)
    # il parametro si ignora, mai un 400 né un 404 sulla rotta esistente.
    if partenariato and get_settings().partenariati_attivo:
        from app.services import partenariato_service  # import locale: modulo a flag

        if partenariato in partenariato_service.MODALITA_FILTRO:
            filters.bando_ids = await partenariato_service.bando_ids_per_modalita(
                primary, partenariato
            )
            if not filters.bando_ids:
                # Nessun bando analizzato con quella modalità: pagina vuota
                # SENZA interrogare il catalogo.
                return Page.build([], 0, page, page_size)
    lookups = await get_lookups(secondary)
    facets = await _facets_per_il_badge(primary, active, lookups)
    return await bandi_service.fetch_bandi(
        secondary,
        filters,
        page,
        page_size,
        sort,
        company_facets=facets,
        totale_regioni=len(lookups.regioni),
    )


@router.get("/{slug}", response_model=BandoDetail)
async def get_bando(
    active: ActiveCompanyDep, primary: PrimaryClient, secondary: SecondaryClient, slug: str
) -> BandoDetail:
    lookups = await get_lookups(secondary)
    facets = await _facets_per_il_badge(primary, active, lookups)
    return await bandi_service.fetch_bando_by_slug(
        secondary, slug, company_facets=facets, totale_regioni=len(lookups.regioni)
    )
