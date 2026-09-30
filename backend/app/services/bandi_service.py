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
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from postgrest.exceptions import APIError

from app.schemas.bando import BandoDetail, BandoListItem, Compatibilita
from app.services.bandi_risoluzione import VISTA_BANDI, carica_per_slug
from app.services.bando_scheda_link import (
    calcola_allegati,
    calcola_cta,
    calcola_link_fonte,
    carica_link_scheda,
    fonte_ufficiale_pubblicabile,
)
from app.services.compatibility import CompanyFacets, compute_compatibilita
from app.services.link_policy import scrub_bando_row
from app.schemas.common import Page

# Campi mostrati nelle card dell'elenco + embed di visualizzazione.
LIST_SELECT = (
    "id,slug,titolo,titolo_breve,descrizione_breve,stato_bando,stato_effettivo,livello,"
    "data_pubblicazione,data_apertura,data_scadenza,"
    "importo_totale_eur,importo_max_per_progetto_eur,ente_erogatore,"
    "tipologie_bando(id,nome),modalita_erogazione(id,nome),"
    "bando_regioni(regioni(id,nome))"
)

# Junction settori/beneficiari/ateco come SOLI id: non si mostrano in lista,
# servono al punteggio di compatibilità e viaggiano nella stessa query. Si
# aggiungono solo quando un punteggio verrà davvero calcolato (le regioni sono
# già embeddate per la visualizzazione).
SCORING_EMBEDS = (
    ",bando_settori(settore_id),bando_beneficiari(beneficiario_id),"
    "bando_codici_ateco(codice_ateco_id)"
)

# Colonne deprecate lette SOLO come ripiego del pulsante e degli allegati
# (contratto DB bandi §5.1). Quando il catalogo le copre con `bando_link` la
# tupla si svuota: API e frontend non cambiano (`bando_scheda_link`).
COLONNE_RIPIEGO_51: tuple[str, ...] = ("link_candidatura", "link_bando", "allegati")


def _detail_select(ripieghi: tuple[str, ...]) -> str:
    return (
        "id,slug,titolo,titolo_breve,descrizione_breve,stato_bando,stato_effettivo,livello,"
        "data_pubblicazione,data_apertura,data_scadenza,ora_apertura,ora_scadenza,"
        "data_pubblicazione_verificata,data_apertura_verificata,data_scadenza_verificata,"
        "importo_totale_eur,importo_max_per_progetto_eur,ente_erogatore,"
        "area_geografica,tematica,contenuto,"
        "fonte_ufficiale_url,fonte_ufficiale_host,fonte_ufficiale_tipo,fonte_ufficiale_stato,"
        "fonte_ufficiale_verificata_at,fonte_ufficiale_e_atto,"
        + "".join(f"{colonna}," for colonna in ripieghi)
        + "tipologie_bando(id,nome),modalita_erogazione(id,nome),programmi(id,nome),"
        "bando_regioni(regioni(id,nome)),bando_settori(settori(id,nome)),"
        "bando_beneficiari(beneficiari(id,nome)),"
        "bando_codici_ateco(codici_ateco(id,codice,descrizione))"
    )


DETAIL_SELECT = _detail_select(COLONNE_RIPIEGO_51)

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
    # Id ammessi (filtro «Ammette partenariato», dal DB primario). None =
    # nessun filtro; lista vuota = nessun bando (pagina vuota, niente query).
    bando_ids: list[int] | None = None


def sanitize_fts_term(term: str) -> str:
    """Rimuove virgole, parentesi, backslash e doppi apici dal termine di ricerca:
    tiene semplice il valore di ``ricerca=wfts(italian).<termine>`` (e stabili i test)."""
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


def build_list_select(filters: BandiFilters, *, include_facets: bool = False) -> str:
    """Select dell'elenco + embed ``!inner`` aliasati per le faccette M:N attive,
    più (solo se serve un punteggio) gli embed id-only per la compatibilità."""
    select = LIST_SELECT
    if include_facets:
        select += SCORING_EMBEDS
    for facet, (alias, junction, id_col) in JUNCTION_FACETS.items():
        if getattr(filters, facet):
            select += f",{alias}:{junction}!inner({id_col})"
    return select


def apply_filters(query, filters: BandiFilters, today: date | None = None):
    """Applica tutti i filtri a un query builder PostgREST (elenco bandi)."""
    today = today or today_italy()

    # La vista contiene solo bandi pubblicati; lo slug non nullo resta come
    # difesa (una riga senza slug non si può mostrare né linkare).
    query = query.not_.is_("slug", "null")

    if filters.q:
        term = sanitize_fts_term(filters.q)
        if term:
            # `ricerca`: colonna tsvector generata, esposta dalla vista `bando_pubblico`
            # (titolo, titolo_breve, descrizione_breve, titolo_raw), contratto DB bandi
            # v11 §3: un solo `@@` per riga invece di un `to_tsvector` per colonna.
            query = query.filter("ricerca", "wfts(italian)", term)
    if filters.stato:
        query = query.in_("stato_effettivo", filters.stato)
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
    if filters.bando_ids is not None:
        query = query.in_("id", filters.bando_ids)

    for facet, (alias, _junction, id_col) in JUNCTION_FACETS.items():
        ids = getattr(filters, facet)
        if ids:
            query = query.in_(f"{alias}.{id_col}", ids)

    return query


# I due segmenti poggiano su `stato_effettivo`, lo stato che il catalogo
# calcola alla lettura con data e ora di Roma (contratto DB bandi §4): la
# scadenza è già dentro lo stato, quindi `today` non serve più (resta nella
# firma, che usano anche alert e partenariati). 'sospeso', 'revocato' e
# qualunque stato non previsto non sono né aperti né chiusi e restano fuori
# da ENTRAMBI. Uno stato NULL conta come aperto: un bando non deve sparire
# in silenzio dalle liste. PostgREST mette in AND questi filtri con gli
# altri, ricerca full-text compresa.

STATI_APERTI = ("aperto", "in apertura prossimamente")


def apply_open_tier(query, today: date):
    """Solo i bandi aperti o in apertura (o senza stato)."""
    # Valori fra doppi apici: "in apertura prossimamente" contiene spazi.
    stati = ",".join(f'"{stato}"' for stato in STATI_APERTI)
    return query.or_(f"stato_effettivo.in.({stati}),stato_effettivo.is.null")


def apply_closed_tier(query, today: date):
    """Solo i bandi chiusi."""
    return query.eq("stato_effettivo", "chiuso")


def _lookup(value: dict | None) -> dict | None:
    return value if value else None


def _flatten_junction(rows: list | None, key: str) -> list[dict]:
    return [row[key] for row in (rows or []) if isinstance(row, dict) and row.get(key)]


def _junction_ids(rows: list | None, id_key: str, nested_key: str) -> list[int]:
    """Id di una junction, robusto ai due embed: id-only (elenco, es.
    ``{settore_id: X}``) o annidato (dettaglio, es. ``{settori: {id: X}}``)."""
    ids: list[int] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        value = row.get(id_key)
        if value is None:
            nested = row.get(nested_key)
            value = nested.get("id") if isinstance(nested, dict) else None
        if value is not None:
            ids.append(value)
    return ids


def bando_facet_ids(row: dict) -> dict:
    """Id dei facet del bando per il calcolo di compatibilità."""
    return {
        "regioni": _junction_ids(row.get("bando_regioni"), "regione_id", "regioni"),
        "ateco": _junction_ids(row.get("bando_codici_ateco"), "codice_ateco_id", "codici_ateco"),
        "settori": _junction_ids(row.get("bando_settori"), "settore_id", "settori"),
        "beneficiari": _junction_ids(row.get("bando_beneficiari"), "beneficiario_id", "beneficiari"),
    }


def _compat_for_row(
    row: dict, company_facets: "CompanyFacets | None", totale_regioni: int
) -> Compatibilita | None:
    if company_facets is None:
        return None
    result = compute_compatibilita(
        company_facets, bando_facet_ids(row), totale_regioni=totale_regioni
    )
    return Compatibilita(**result) if result else None


def map_list_item(row: dict) -> BandoListItem:
    return BandoListItem(
        id=row["id"],
        slug=row["slug"],
        titolo=row.get("titolo"),
        titolo_breve=row.get("titolo_breve"),
        descrizione_breve=row.get("descrizione_breve"),
        stato_bando=row.get("stato_bando"),
        stato_effettivo=row.get("stato_effettivo"),
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


def _ora(value: Any) -> time | None:
    """Ora di Roma dal catalogo, tollerante: «24:00» («entro le ore 24») o un
    valore non valido diventano None, che per il contratto DB bandi §4 vuol
    dire «tutta la giornata». L'eventuale fuso si toglie (è già ora di Roma), e
    così le frazioni di secondo: l'API espone sempre «HH:MM:SS»."""
    if isinstance(value, time):
        return value.replace(tzinfo=None, microsecond=0)
    if not isinstance(value, str):
        return None
    try:
        return time.fromisoformat(value.strip()).replace(tzinfo=None, microsecond=0)
    except ValueError:
        return None


def map_detail(row: dict, link: list[dict] | None = None) -> BandoDetail:
    """Dettaglio dalla riga (già filtrata da `scrub_bando_row`) e dalle righe
    `bando_link` del bando (None o [] = solo i ripieghi della riga)."""
    base = map_list_item(row).model_dump()
    link = link or []
    fonte_url, fonte_host = fonte_ufficiale_pubblicabile(row)
    return BandoDetail(
        **base,
        area_geografica=row.get("area_geografica"),
        tematica=row.get("tematica") or [],
        ora_apertura=_ora(row.get("ora_apertura")),
        ora_scadenza=_ora(row.get("ora_scadenza")),
        data_pubblicazione_verificata=row.get("data_pubblicazione_verificata"),
        data_apertura_verificata=row.get("data_apertura_verificata"),
        data_scadenza_verificata=row.get("data_scadenza_verificata"),
        contenuto=normalize_contenuto(row.get("contenuto")),
        cta=calcola_cta(row, link),
        link_fonte=calcola_link_fonte(row),
        allegati=calcola_allegati(row, link),
        fonte_ufficiale_url=fonte_url,
        fonte_ufficiale_host=fonte_host,
        fonte_ufficiale_tipo=row.get("fonte_ufficiale_tipo"),
        fonte_ufficiale_stato=row.get("fonte_ufficiale_stato"),
        fonte_ufficiale_verificata_at=row.get("fonte_ufficiale_verificata_at"),
        fonte_ufficiale_e_atto=row.get("fonte_ufficiale_e_atto"),
        programma=_lookup(row.get("programmi")),
        settori=_flatten_junction(row.get("bando_settori"), "settori"),
        beneficiari=_flatten_junction(row.get("bando_beneficiari"), "beneficiari"),
        codici_ateco=_flatten_junction(row.get("bando_codici_ateco"), "codici_ateco"),
    )


async def _pagina_segmento(costruisci, offset: int, quante: int) -> tuple[list[dict], int]:
    """Righe `offset..offset+quante-1` di un segmento e suo conteggio esatto.

    `costruisci` crea da zero la query del segmento a ogni chiamata: i builder
    di postgrest-py accumulano i parametri e non si riusano. Con il conteggio
    esatto, un offset oltre le righe del segmento fa rispondere al catalogo
    «intervallo non soddisfacibile» (PGRST103): vale come pagina vuota, e il
    conteggio si rilegge con una richiesta senza offset. Ogni altro errore
    risale invariato."""
    try:
        resp = await costruisci().range(offset, offset + quante - 1).execute()
    except APIError as exc:
        if exc.code != "PGRST103":
            raise
        resp = await costruisci().limit(1).execute()
        return [], resp.count or 0
    return list(resp.data or []), resp.count or 0


async def fetch_bandi(
    secondary,
    filters: BandiFilters,
    page: int,
    page_size: int,
    sort: str,
    *,
    company_facets: "CompanyFacets | None" = None,
    totale_regioni: int = 0,
) -> Page[BandoListItem]:
    """Elenco paginato in due segmenti: prima i bandi non chiusi, poi i chiusi
    — sempre in coda, qualunque ordinamento. PostgREST non sa ordinare per
    espressioni, quindi il confine è realizzato con due query complementari;
    la pagina a cavallo del confine unisce le due code. Una pagina oltre
    l'ultima è vuota, con il totale esatto."""
    if filters.bando_ids is not None and not filters.bando_ids:
        # `id=in.()` non va mandato al catalogo: nessun id ammesso = pagina vuota.
        return Page.build([], 0, page, page_size)
    column, desc_open, desc_closed = SORT_OPTIONS.get(sort, SORT_OPTIONS[DEFAULT_SORT])
    offset = (page - 1) * page_size
    today = today_italy()
    select = build_list_select(filters, include_facets=company_facets is not None)

    def segmento(tier, desc: bool):
        """Query nuova di un segmento: conteggio esatto, filtri, ordinamento."""
        q = secondary.table(VISTA_BANDI).select(select, count="exact")
        q = tier(apply_filters(q, filters, today), today)
        return q.order(column, desc=desc, nullsfirst=False).order("id", desc=False)

    rows, open_count = await _pagina_segmento(
        lambda: segmento(apply_open_tier, desc_open), offset, page_size
    )

    need = page_size - len(rows)
    if need > 0:
        # Offset dentro il segmento dei chiusi: 0 se la pagina è a cavallo del
        # confine, oltre se la pagina è tutta nel segmento dei chiusi.
        closed_offset = max(0, offset - open_count)
        closed_rows, closed_count = await _pagina_segmento(
            lambda: segmento(apply_closed_tier, desc_closed), closed_offset, need
        )
        rows.extend(closed_rows)
    else:
        # Pagina piena di non chiusi: serve comunque il conteggio dei chiusi
        # per il totale della paginazione.
        closed_resp = await segmento(apply_closed_tier, desc_closed).limit(1).execute()
        closed_count = closed_resp.count or 0

    total = open_count + closed_count

    # Le due query non condividono uno snapshot: un bando che cambia segmento
    # tra l'una e l'altra (pipeline di ingestione) comparirebbe in entrambe le
    # code — dedup per id, la pagina si riassesta al refetch successivo.
    seen_ids: set = set()
    items = []
    for row in rows:
        if row["id"] in seen_ids:
            continue
        seen_ids.add(row["id"])
        item = map_list_item(row)
        item.compatibilita = _compat_for_row(row, company_facets, totale_regioni)
        items.append(item)
    return Page.build(items, total, page, page_size)


async def fetch_bando_for_ai(secondary, slug: str) -> dict:
    """Riga grezza del bando per la pipeline AI-check (la chiave della
    cache estrazioni è l'hash del testo serializzato, vedi
    `compute_content_hash`). `contenuto` è già normalizzato (gestione
    del doppio-encoding). Nessuna lettura di `bando_link`: l'input
    dell'AI-check resta la riga, con `stato_bando` e il jsonb `allegati`.

    Stessa risoluzione del dettaglio (`carica_per_slug`): uno slug spostato
    restituisce la riga del master (id e slug canonici), uno ritirato solleva
    `BandoRitiratoError` (410)."""
    row = await carica_per_slug(secondary, slug, DETAIL_SELECT)
    row["contenuto"] = normalize_contenuto(row.get("contenuto"))
    # I link ai domini esclusi (concorrenti) non devono arrivare nemmeno
    # al testo del prompt: il modello li citerebbe nel report.
    return scrub_bando_row(row)


async def fetch_bando_by_slug(
    secondary,
    slug: str,
    *,
    company_facets: "CompanyFacets | None" = None,
    totale_regioni: int = 0,
) -> BandoDetail:
    """Dettaglio per slug. Slug spostato (storico 301 o fusione) → dettaglio
    del master, con lo slug canonico in `slug`; ritirato → 410; altrimenti
    404 (vedi `bandi_risoluzione.carica_per_slug`). Pulsanti e allegati dalle
    righe `bando_link` del bando risolto più i ripieghi della riga; se quella
    lettura non riesce, solo i ripieghi (`bando_scheda_link`)."""
    row = await carica_per_slug(secondary, slug, DETAIL_SELECT)
    # Normalizzare PRIMA di filtrare: un `contenuto` doppio-encodato non
    # verrebbe attraversato dal filtro dei link (map_detail è idempotente).
    row["contenuto"] = normalize_contenuto(row.get("contenuto"))
    row = scrub_bando_row(row)
    link = await carica_link_scheda(secondary, row["id"])
    detail = map_detail(row, link)
    detail.compatibilita = _compat_for_row(row, company_facets, totale_regioni)
    return detail
