"""Interrogazione del catalogo bandi (DB secondario, sola lettura).

Meccanica dei filtri M:N con PostgREST: per ogni dimensione filtrata si
aggiunge un embed ``!inner`` ALIASATO sulla junction (es.
``f_reg:bando_regioni!inner(regione_id)``) e si filtra con
``in`` sull'alias (``f_reg.regione_id=in.(...)``). L'alias è necessario
perché la stessa junction può comparire anche come embed di visualizzazione.
Semantica: OR dentro la stessa faccetta, AND tra faccette diverse.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

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

# ordinamento -> (colonna, desc tra i non chiusi, desc tra i chiusi).
# I chiusi vanno sempre in coda: con "scadenza più vicina" tra i chiusi si
# mostra prima la chiusura più recente (asc mostrerebbe prima i più vecchi).
SORT_OPTIONS = {
    "scadenza_asc": ("data_scadenza", False, True),
    "scadenza_desc": ("data_scadenza", True, True),
    "pubblicazione_desc": ("data_pubblicazione", True, True),
    "importo_desc": ("importo_totale_eur", True, True),
}

DEFAULT_SORT = "pubblicazione_desc"


def today_italy() -> date:
    """Le date dei bandi sono date italiane: "oggi" va calcolato su Europe/Rome,
    non sul fuso del server (in UTC la data cambia due ore dopo)."""
    return datetime.now(ZoneInfo("Europe/Rome")).date()


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
    """Rimuove i caratteri che romperebbero la grammatica di ``or=(...)`` di PostgREST
    (virgole, parentesi, backslash e doppi apici che aprirebbero un token quotato)."""
    return re.sub(r'[,()\\"]', " ", term).strip()


def normalize_contenuto(value: Any) -> dict | None:
    """Alcune righe del DB secondario hanno ``contenuto`` come stringa JSON
    doppio-encodata invece che come oggetto: va decodificata, altrimenti il
    modello (dict) rifiuterebbe la stringa e il dettaglio andrebbe in errore."""
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def build_list_select(filters: BandiFilters) -> str:
    """Select dell'elenco + embed ``!inner`` aliasati per le faccette M:N attive."""
    select = LIST_SELECT
    for facet, (alias, junction, id_col) in JUNCTION_FACETS.items():
        if getattr(filters, facet):
            select += f",{alias}:{junction}!inner({id_col})"
    return select


def apply_filters(query, filters: BandiFilters, today: date | None = None):
    """Applica tutti i filtri a un query builder PostgREST (elenco bandi)."""
    today = today or today_italy()

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


# Un bando è "chiuso" se lo dice il catalogo O se la scadenza è passata: la
# doppia condizione regge anche quando stato_bando non è aggiornato dalla
# pipeline. I due filtri sono complementari (null-safe): ogni riga finisce in
# esattamente uno dei due segmenti. PostgREST mette in AND i parametri ``or``
# ripetuti, quindi convivono anche con l'``or`` della ricerca full-text.


def apply_open_tier(query, today: date):
    """Solo i bandi non chiusi: stato diverso da 'chiuso' E scadenza non passata
    (i null contano come non chiusi: bandi a sportello o senza data)."""
    return query.or_("stato_bando.neq.chiuso,stato_bando.is.null").or_(
        f"data_scadenza.gte.{today.isoformat()},data_scadenza.is.null"
    )


def apply_closed_tier(query, today: date):
    """Solo i bandi chiusi: stato 'chiuso' O scadenza passata."""
    return query.or_(f"stato_bando.eq.chiuso,data_scadenza.lt.{today.isoformat()}")


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
        contenuto=normalize_contenuto(row.get("contenuto")),
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
    """Elenco paginato in due segmenti: prima i bandi non chiusi, poi i chiusi
    — sempre in coda, qualunque ordinamento. PostgREST non sa ordinare per
    espressioni, quindi il confine è realizzato con due query complementari;
    la pagina a cavallo del confine unisce le due code."""
    column, desc_open, desc_closed = SORT_OPTIONS.get(sort, SORT_OPTIONS[DEFAULT_SORT])
    offset = (page - 1) * page_size
    today = today_italy()
    select = build_list_select(filters)

    open_q = secondary.table("bando").select(select, count="exact")
    open_q = apply_open_tier(apply_filters(open_q, filters, today), today)
    open_q = (
        open_q.order(column, desc=desc_open, nullsfirst=False)
        .order("id", desc=False)
        .range(offset, offset + page_size - 1)
    )
    open_resp = await open_q.execute()
    open_count = open_resp.count or 0
    rows = list(open_resp.data)

    closed_q = secondary.table("bando").select(select, count="exact")
    closed_q = apply_closed_tier(apply_filters(closed_q, filters, today), today)
    closed_q = closed_q.order(column, desc=desc_closed, nullsfirst=False).order(
        "id", desc=False
    )

    need = page_size - len(rows)
    if need > 0:
        # Offset dentro il segmento dei chiusi: 0 se la pagina è a cavallo del
        # confine, oltre se la pagina è tutta nel segmento dei chiusi.
        closed_offset = max(0, offset - open_count)
        closed_resp = await closed_q.range(closed_offset, closed_offset + need - 1).execute()
        rows.extend(closed_resp.data)
    else:
        # Pagina piena di non chiusi: serve comunque il conteggio dei chiusi
        # per il totale della paginazione.
        closed_resp = await closed_q.limit(1).execute()

    total = open_count + (closed_resp.count or 0)

    # Le due query non condividono uno snapshot: un bando che cambia segmento
    # tra l'una e l'altra (pipeline di ingestione) comparirebbe in entrambe le
    # code — dedup per id, la pagina si riassesta al refetch successivo.
    seen_ids: set = set()
    items = []
    for row in rows:
        if row["id"] in seen_ids:
            continue
        seen_ids.add(row["id"])
        items.append(map_list_item(row))
    return Page.build(items, total, page, page_size)


async def fetch_bando_for_ai(secondary, slug: str) -> dict:
    """Riga grezza del bando per la pipeline AI-check: tutti i campi del
    dettaglio più hash_bando/updated_at (chiave della cache estrazioni).
    `contenuto` è già normalizzato (gestione del doppio-encoding)."""
    resp = (
        await secondary.table("bando")
        .select(DETAIL_SELECT + ",hash_bando,updated_at")
        .eq("slug", slug)
        .eq("stato_processing", "completed")
        .limit(1)
        .execute()
    )
    if not resp.data:
        raise NotFoundError("Bando non trovato")
    row = dict(resp.data[0])
    row["contenuto"] = normalize_contenuto(row.get("contenuto"))
    return row


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
