from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, SecondaryClient
from app.core.errors import BadRequestError
from app.schemas.bando import BandoDetail, BandoListItem
from app.schemas.common import Page
from app.services import bandi_service
from app.services.bandi_service import SORT_OPTIONS, BandiFilters

router = APIRouter(prefix="/bandi", tags=["bandi"])

_VALID_STATI = {"aperto", "chiuso", "in apertura prossimamente"}
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
    stato: str | None = Query(default=None, description="Stati separati da virgola"),
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
    stati = [s for s in (stato.split(",") if stato else []) if s]
    invalid = set(stati) - _VALID_STATI
    if invalid:
        raise BadRequestError(f"Stato bando non valido: {', '.join(sorted(invalid))}")
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
    _user: CurrentUser,
    secondary: SecondaryClient,
    filters: Annotated[BandiFilters, Depends(parse_filters)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
    sort: str = Query(default="scadenza_asc"),
) -> Page[BandoListItem]:
    if sort not in SORT_OPTIONS:
        raise BadRequestError(f"Ordinamento non valido: {sort}")
    return await bandi_service.fetch_bandi(secondary, filters, page, page_size, sort)


@router.get("/{slug}", response_model=BandoDetail)
async def get_bando(_user: CurrentUser, secondary: SecondaryClient, slug: str) -> BandoDetail:
    return await bandi_service.fetch_bando_by_slug(secondary, slug)
