"""Interrogazione del catalogo bandi (DB secondario, sola lettura).

Meccanica dei filtri M:N con PostgREST: per ogni dimensione filtrata si
aggiunge un embed ``!inner`` ALIASATO sulla junction (es.
``f_reg:bando_regioni!inner(regione_id)``) e si filtra con
``in`` sull'alias (``f_reg.regione_id=in.(...)``). L'alias è necessario
perché la stessa junction può comparire anche come embed di visualizzazione.
Semantica: OR dentro la stessa faccetta, AND tra faccette diverse.
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.core.errors import NotFoundError
from app.schemas.bando import BandoDetail, BandoListItem
from app.schemas.common import Page

# Campi mostrati nelle card dell'elenco + embed di visualizzazione.
LIST_SELECT = (
    "id,slug,titolo,titolo_breve,descrizione_breve,stato_bando,livello,"
    "data_pubblicazione,data_apertura,data_scadenza,"
    "importo_totale_eur,importo_max_per_progetto_eur,ente_erogatore,"
    "tipologie_bando(id,nome),modalita_erogazione(id,nome),"
    "bando_regioni(regioni(id,nome))"
)

DETAIL_SELECT = (
    "id,slug,titolo,titolo_breve,descrizione_raw,descrizione_breve,stato_bando,livello,"
    "data_pubblicazione,data_apertura,data_scadenza,"
    "importo_totale_eur,importo_max_per_progetto_eur,ente_erogatore,"
    "area_geografica,tematica,link_bando,link_candidatura,contenuto,allegati,"
    "tipologie_bando(id,nome),modalita_erogazione(id,nome),programmi(id,nome),"
    "bando_regioni(regioni(id,nome)),bando_settori(settori(id,nome)),"
    "bando_beneficiari(beneficiari(id,nome)),"
    "bando_codici_ateco(codici_ateco(id,codice,descrizione))"
)

# faccetta -> (alias, junction, colonna id)
JUNCTION_FACETS = {
    "regioni": ("f_reg", "bando_regioni", "regione_id"),
    "settori": ("f_set", "bando_settori", "settore_id"),
    "beneficiari": ("f_ben", "bando_beneficiari", "beneficiario_id"),
    "codici_ateco": ("f_ate", "bando_codici_ateco", "codice_ateco_id"),
}

SORT_OPTIONS = {
    "scadenza_asc": ("data_scadenza", False),
    "scadenza_desc": ("data_scadenza", True),
    "pubblicazione_desc": ("data_pubblicazione", True),
    "importo_desc": ("importo_totale_eur", True),
}


@dataclass
class BandiFilters:
    q: str | None = None
    stato: list[str] = field(default_factory=list)
    livello: str | None = None
    tipologie: list[int] = field(default_factory=list)
    modalita: list[int] = field(default_factory=list)
    programmi: list[int] = field(default_factory=list)
    regioni: list[int] = field(default_factory=list)
    settori: list[int] = field(default_factory=list)
    beneficiari: list[int] = field(default_factory=list)
    codici_ateco: list[int] = field(default_factory=list)
    importo_min: int | None = None
    importo_max: int | None = None
    scadenza_da: date | None = None
    scadenza_a: date | None = None
    scade_entro_giorni: int | None = None


def sanitize_fts_term(term: str) -> str:
    """Rimuove i caratteri che romperebbero la grammatica di ``or=(...)`` di PostgREST."""
    return re.sub(r"[,()\\]", " ", term).strip()


def build_list_select(filters: BandiFilters) -> str:
    """Select dell'elenco + embed ``!inner`` aliasati per le faccette M:N attive."""
    select = LIST_SELECT
    for facet, (alias, junction, id_col) in JUNCTION_FACETS.items():
        if getattr(filters, facet):
            select += f",{alias}:{junction}!inner({id_col})"
    return select


def apply_filters(query, filters: BandiFilters, today: date | None = None):
    """Applica tutti i filtri a un query builder PostgREST (elenco bandi)."""
    today = today or date.today()

    # Ridondante rispetto alla RLS del secondario, ma esplicita il contratto.
    query = query.eq("stato_processing", "completed").not_.is_("slug", "null")

    if filters.q:
        term = sanitize_fts_term(filters.q)
        if term:
            query = query.or_(
                f"titolo_raw.wfts(italian).{term},descrizione_raw.wfts(italian).{term}"
            )
    if filters.stato:
        query = query.in_("stato_bando", filters.stato)
    if filters.livello:
        query = query.eq("livello", filters.livello)
    if filters.tipologie:
        query = query.in_("tipologia_bando_id", filters.tipologie)
    if filters.modalita:
        query = query.in_("modalita_erogazione_id", filters.modalita)
    if filters.programmi:
        query = query.in_("programma_id", filters.programmi)
    if filters.importo_min is not None:
        query = query.gte("importo_totale_eur", filters.importo_min)
    if filters.importo_max is not None:
        query = query.lte("importo_totale_eur", filters.importo_max)
    if filters.scadenza_da:
        query = query.gte("data_scadenza", filters.scadenza_da.isoformat())
    if filters.scadenza_a:
        query = query.lte("data_scadenza", filters.scadenza_a.isoformat())
    if filters.scade_entro_giorni is not None:
        query = query.gte("data_scadenza", today.isoformat()).lte(
            "data_scadenza", (today + timedelta(days=filters.scade_entro_giorni)).isoformat()
        )

    for facet, (alias, _junction, id_col) in JUNCTION_FACETS.items():
        ids = getattr(filters, facet)
        if ids:
            query = query.in_(f"{alias}.{id_col}", ids)

    return query


def _lookup(value: dict | None) -> dict | None:
    return value if value else None


def _flatten_junction(rows: list | None, key: str) -> list[dict]:
    return [row[key] for row in (rows or []) if isinstance(row, dict) and row.get(key)]


def map_list_item(row: dict) -> BandoListItem:
    return BandoListItem(
        id=row["id"],
        slug=row["slug"],
        titolo=row.get("titolo"),
        titolo_breve=row.get("titolo_breve"),
        descrizione_breve=row.get("descrizione_breve"),
        stato_bando=row.get("stato_bando"),
        livello=row.get("livello"),
        data_pubblicazione=row.get("data_pubblicazione"),
        data_apertura=row.get("data_apertura"),
        data_scadenza=row.get("data_scadenza"),
        importo_totale_eur=row.get("importo_totale_eur"),
        importo_max_per_progetto_eur=row.get("importo_max_per_progetto_eur"),
        ente_erogatore=row.get("ente_erogatore"),
        tipologia=_lookup(row.get("tipologie_bando")),
        modalita_erogazione=_lookup(row.get("modalita_erogazione")),
        regioni=_flatten_junction(row.get("bando_regioni"), "regioni"),
    )


def map_detail(row: dict) -> BandoDetail:
    base = map_list_item(row).model_dump()
    return BandoDetail(
        **base,
        area_geografica=row.get("area_geografica"),
        tematica=row.get("tematica") or [],
        link_bando=row.get("link_bando"),
        link_candidatura=row.get("link_candidatura"),
        contenuto=row.get("contenuto"),
        allegati=row.get("allegati") or [],
        programma=_lookup(row.get("programmi")),
        settori=_flatten_junction(row.get("bando_settori"), "settori"),
        beneficiari=_flatten_junction(row.get("bando_beneficiari"), "beneficiari"),
        codici_ateco=_flatten_junction(row.get("bando_codici_ateco"), "codici_ateco"),
    )


async def fetch_bandi(
    secondary,
    filters: BandiFilters,
    page: int,
    page_size: int,
    sort: str,
) -> Page[BandoListItem]:
    column, desc = SORT_OPTIONS.get(sort, SORT_OPTIONS["scadenza_asc"])
    offset = (page - 1) * page_size

    query = secondary.table("bando").select(build_list_select(filters), count="exact")
    query = apply_filters(query, filters)
    query = (
        query.order(column, desc=desc, nullsfirst=False)
        .order("id", desc=False)
        .range(offset, offset + page_size - 1)
    )

    resp = await query.execute()
    items = [map_list_item(row) for row in resp.data]
    return Page.build(items, resp.count or 0, page, page_size)


async def fetch_bando_by_slug(secondary, slug: str) -> BandoDetail:
    resp = (
        await secondary.table("bando")
        .select(DETAIL_SELECT)
        .eq("slug", slug)
        .eq("stato_processing", "completed")
        .limit(1)
        .execute()
    )
    if not resp.data:
        raise NotFoundError("Bando non trovato")
    return map_detail(resp.data[0])
