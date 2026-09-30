"""Test del builder dei filtri PostgREST per l'elenco bandi.

Usano il query builder reale di postgrest-py SENZA rete: si costruisce la
query e si ispezionano i parametri URL generati.
"""

from datetime import date
from types import SimpleNamespace
from typing import Annotated
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import Depends, FastAPI
from postgrest import AsyncPostgrestClient

from app.api.routers.bandi import parse_filters
from app.core.errors import register_exception_handlers
from app.services.bando_alert_service import carica_candidati
from app.services.bandi_service import (
    BandiFilters,
    apply_closed_tier,
    apply_filters,
    apply_open_tier,
    build_list_select,
    fetch_bandi,
    map_detail,
    map_list_item,
    normalize_contenuto,
    sanitize_fts_term,
    today_italy,
)

TODAY = date(2026, 7, 3)


@pytest.fixture
def client() -> AsyncPostgrestClient:
    return AsyncPostgrestClient("http://localhost:54321/rest/v1")


def params_of(query) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for key, value in query.request.params.multi_items():
        out.setdefault(key, []).append(value)
    return out


def build(client, filters: BandiFilters):
    query = client.from_("bando_pubblico").select(build_list_select(filters))
    return apply_filters(query, filters, today=TODAY)


class TestSelect:
    def test_base_select_has_display_embeds_only(self):
        select = build_list_select(BandiFilters())
        assert "bando_regioni(regioni(id,nome))" in select
        assert "!inner" not in select

    def test_select_ha_stato_effettivo_e_stato_bando(self):
        colonne = build_list_select(BandiFilters()).split(",")
        assert "stato_effettivo" in colonne
        assert "stato_bando" in colonne  # resta, solo informativo

    def test_active_facets_add_aliased_inner_embeds(self):
        filters = BandiFilters(regioni=[1], settori=[2, 3])
        select = build_list_select(filters)
        assert "f_reg:bando_regioni!inner(regione_id)" in select
        assert "f_set:bando_settori!inner(settore_id)" in select
        assert "f_ben" not in select
        assert "f_ate" not in select
        # l'embed di visualizzazione resta accanto a quello di filtro
        assert "bando_regioni(regioni(id,nome))" in select


class TestBaseFilters:
    def test_solo_slug_non_nullo_senza_stato_processing(self, client):
        # La vista è già filtrata sui pubblicati: resta la sola difesa sullo slug.
        params = params_of(build(client, BandiFilters()))
        assert "stato_processing" not in params
        assert params["slug"] == ["not.is.null"]
        assert set(params) == {"select", "slug"}

    def test_stato_in_su_stato_effettivo(self, client):
        params = params_of(build(client, BandiFilters(stato=["aperto", "in apertura prossimamente"])))
        assert params["stato_effettivo"] == ["in.(aperto,in apertura prossimamente)"]
        assert "stato_bando" not in params

    def test_direct_columns(self, client):
        filters = BandiFilters(
            livello="flash_bando",
            tipologie=[3],
            modalita=[1, 2],
            programmi=[4],
            importo_min=10_000,
            importo_max=1_000_000,
        )
        params = params_of(build(client, filters))
        assert params["livello"] == ["eq.flash_bando"]
        assert params["tipologia_bando_id"] == ["in.(3)"]
        assert params["modalita_erogazione_id"] == ["in.(1,2)"]
        assert params["programma_id"] == ["in.(4)"]
        assert params["importo_totale_eur"] == ["gte.10000", "lte.1000000"]

    def test_scadenza_range(self, client):
        filters = BandiFilters(scadenza_da=date(2026, 8, 1), scadenza_a=date(2026, 12, 31))
        params = params_of(build(client, filters))
        assert params["data_scadenza"] == ["gte.2026-08-01", "lte.2026-12-31"]

    def test_scade_entro_giorni(self, client):
        params = params_of(build(client, BandiFilters(scade_entro_giorni=30)))
        assert params["data_scadenza"] == ["gte.2026-07-03", "lte.2026-08-02"]


class TestJunctionFilters:
    def test_junction_filter_targets_alias(self, client):
        filters = BandiFilters(regioni=[5, 9], codici_ateco=[12])
        params = params_of(build(client, filters))
        assert params["f_reg.regione_id"] == ["in.(5,9)"]
        assert params["f_ate.codice_ateco_id"] == ["in.(12)"]
        assert "f_set.settore_id" not in params


class TestFullText:
    def test_fts_uses_wfts_italian_on_ricerca(self, client):
        # Una sola colonna tsvector generata (contratto DB bandi v11 §3/§7),
        # non più l'``or`` a cinque rami.
        params = params_of(build(client, BandiFilters(q="transizione digitale")))
        assert params["ricerca"] == ["wfts(italian).transizione digitale"]
        assert "or" not in params

    def test_sanitize_strips_grammar_breaking_chars(self):
        assert sanitize_fts_term("a,b(c)d\\e") == "a b c d e"
        assert sanitize_fts_term("  PNRR  ") == "PNRR"

    def test_sanitize_strips_double_quotes(self):
        # I doppi apici aprirebbero un token quotato mai chiuso in or=(...).
        assert '"' not in sanitize_fts_term('"bando energia" 2024')

    def test_sanitized_term_goes_to_ricerca(self, client):
        params = params_of(build(client, BandiFilters(q='"energia", (PNRR)')))
        [value] = params["ricerca"]
        assert value.startswith("wfts(italian).")
        assert not set(value.removeprefix("wfts(italian).")) & set(',()\\"')

    def test_blank_term_after_sanitize_adds_no_ricerca(self, client):
        params = params_of(build(client, BandiFilters(q="(),")))
        assert "ricerca" not in params
        assert "or" not in params


class TestSorting:
    def test_sort_desc_puts_nulls_last(self, client):
        query = build(client, BandiFilters()).order(
            "importo_totale_eur", desc=True, nullsfirst=False
        ).order("id", desc=False)
        params = params_of(query)
        [value] = params["order"]
        assert "importo_totale_eur.desc" in value
        assert "nullsfirst" not in value
        assert value.endswith("id.asc")


SEGMENTO_APERTI = (
    '(stato_effettivo.in.("aperto","in apertura prossimamente"),stato_effettivo.is.null)'
)


class TestTiers:
    """I due segmenti poggiano su ``stato_effettivo`` (contratto DB bandi §4):
    aperti = aperto, in apertura o NULL; chiusi = chiuso."""

    def test_open_tier_su_stato_effettivo(self, client):
        params = params_of(apply_open_tier(build(client, BandiFilters()), TODAY))
        assert params["or"] == [SEGMENTO_APERTI]
        assert "stato_bando" not in params
        assert "data_scadenza" not in params

    def test_closed_tier_su_stato_effettivo(self, client):
        params = params_of(apply_closed_tier(build(client, BandiFilters()), TODAY))
        assert params["stato_effettivo"] == ["eq.chiuso"]
        assert "or" not in params
        assert "data_scadenza" not in params

    def test_segmenti_non_dipendono_dalla_data(self, client):
        # La scadenza (con l'ora di Roma) è già dentro stato_effettivo.
        for tier in (apply_open_tier, apply_closed_tier):
            oggi = params_of(tier(build(client, BandiFilters()), TODAY))
            altro = params_of(tier(build(client, BandiFilters()), date(2030, 1, 1)))
            assert oggi == altro

    def test_tier_coexists_with_fts(self, client):
        # La ricerca full-text è un filtro a sé su ``ricerca``.
        params = params_of(apply_open_tier(build(client, BandiFilters(q="energia")), TODAY))
        assert params["ricerca"] == ["wfts(italian).energia"]
        assert params["or"] == [SEGMENTO_APERTI]
        params = params_of(apply_closed_tier(build(client, BandiFilters(q="energia")), TODAY))
        assert params["ricerca"] == ["wfts(italian).energia"]
        assert params["stato_effettivo"] == ["eq.chiuso"]

    def test_filtro_stato_utente_in_and_col_segmento(self, client):
        # Il filtro dell'utente e il segmento sono due parametri distinti che
        # PostgREST mette in AND.
        filtri = BandiFilters(stato=["chiuso"])
        params = params_of(apply_closed_tier(build(client, filtri), TODAY))
        assert params["stato_effettivo"] == ["in.(chiuso)", "eq.chiuso"]

    def test_today_italy_is_a_date(self):
        assert isinstance(today_italy(), date)


# --- Valutatore minimo dei filtri dei segmenti -------------------------------
# Applica alle righe i parametri ``or`` VERI generati dal builder, con la logica
# a tre valori di SQL (None = NULL): così i casi sotto verificano la semantica
# dei segmenti riga per riga, non solo la forma delle stringhe. Copre la sola
# grammatica usata dai segmenti: or/and annidati, eq/neq/lt/gte, in, is.null.


def _split_top(expr: str) -> list[str]:
    """Divide sulle virgole di primo livello (fuori da parentesi e doppi apici)."""
    parts, current, depth, quoted = [], "", 0, False
    for ch in expr:
        if ch == '"':
            quoted = not quoted
        elif not quoted and ch == "(":
            depth += 1
        elif not quoted and ch == ")":
            depth -= 1
        elif not quoted and depth == 0 and ch == ",":
            parts.append(current)
            current = ""
            continue
        current += ch
    parts.append(current)
    return parts


def _or3(values: list) -> bool | None:
    if any(v is True for v in values):
        return True
    return None if any(v is None for v in values) else False


def _and3(values: list) -> bool | None:
    if any(v is False for v in values):
        return False
    return None if any(v is None for v in values) else True


def valuta(cond: str, row: dict) -> bool | None:
    for prefix, combine in (("or(", _or3), ("and(", _and3)):
        if cond.startswith(prefix):
            return combine([valuta(c, row) for c in _split_top(cond[len(prefix):-1])])
    column, op, arg = cond.split(".", 2)
    value = row[column]
    if op == "is":
        assert arg == "null"
        return value is None
    if value is None:
        return None  # confronti con NULL: né veri né falsi
    if op == "in":
        return value in [v.strip('"') for v in _split_top(arg[1:-1])]
    return {"eq": value == arg, "neq": value != arg, "lt": value < arg, "gte": value >= arg}[op]


def segmenti_di(client, stato: str | None) -> set[str]:
    """In quali segmenti finisce una riga con questo ``stato_effettivo``:
    come WHERE, conta solo il vero."""
    row = {"stato_effettivo": stato}
    out = set()
    for nome, tier in (("aperti", apply_open_tier), ("chiusi", apply_closed_tier)):
        params = params_of(tier(client.from_("bando_pubblico").select("id"), TODAY))
        condizioni = [f"or{cond}" for cond in params.pop("or", [])]
        params.pop("select")
        condizioni += [f"{col}.{val}" for col, vals in params.items() for val in vals]
        # nessun filtro sfugge al valutatore
        assert all(c.startswith(("or(", "stato_effettivo.")) for c in condizioni)
        if all(valuta(cond, row) is True for cond in condizioni):
            out.add(nome)
    return out


class TestSegmentiPerStato:
    """Contratto DB bandi §4: 'sospeso' e 'revocato' non sono né aperti né
    chiusi, e nemmeno gli stati non previsti. Uno stato NULL resta fra gli
    aperti: un bando non deve sparire in silenzio dalle liste."""

    @pytest.mark.parametrize(
        ("stato", "atteso"),
        [
            ("aperto", {"aperti"}),
            ("in apertura prossimamente", {"aperti"}),
            ("chiuso", {"chiusi"}),
            (None, {"aperti"}),
            ("sospeso", set()),
            ("revocato", set()),
            ("pippo", set()),
            ("Aperto", set()),
        ],
    )
    def test_segmento_della_riga(self, client, stato, atteso):
        assert segmenti_di(client, stato) == atteso

    def test_segmenti_complementari_sugli_stati_noti(self, client):
        # Ogni riga con stato noto (o NULL) sta in esattamente un segmento: la
        # somma dei due count resta il totale della paginazione.
        for stato in ("aperto", "in apertura prossimamente", "chiuso", None):
            assert len(segmenti_di(client, stato)) == 1, stato


def bando_row(id_: int) -> dict:
    return {**TestMapping.ROW, "id": id_, "slug": f"bando-{id_}"}


class FakeBandiQuery:
    """Registra la catena di chiamate del builder e risponde con dati canned."""

    def __init__(self, name: str, response: SimpleNamespace):
        self.name = name
        self._response = response
        self.select_kwargs: dict = {}
        self.eq_filters: list[tuple] = []
        self.in_filters: list[tuple] = []
        self.or_filters: list[str] = []
        self.filters: list[tuple[str, str, str]] = []
        self.orders: list[tuple] = []
        self.range_args: tuple | None = None
        self.limit_arg: int | None = None

    def select(self, *args, **kwargs):
        self.select_kwargs = kwargs
        return self

    def eq(self, column, value):
        self.eq_filters.append((column, value))
        return self

    @property
    def not_(self):
        return self

    def is_(self, *args):
        return self

    def in_(self, column, values):
        self.in_filters.append((column, list(values)))
        return self

    def gte(self, *args):
        return self

    def lte(self, *args):
        return self

    def or_(self, filters: str):
        self.or_filters.append(filters)
        return self

    def filter(self, column: str, operator: str, criteria: str):
        self.filters.append((column, operator, criteria))
        return self

    def order(self, column, desc=False, nullsfirst=None):
        self.orders.append((column, desc, nullsfirst))
        return self

    def range(self, start, end):
        self.range_args = (start, end)
        return self

    def limit(self, size):
        self.limit_arg = size
        return self

    async def execute(self):
        return self._response


class FakeSecondary:
    """Consegna una risposta per query nell'ordine di creazione
    (fetch_bandi interroga prima i non chiusi, poi i chiusi)."""

    def __init__(self, responses: list[SimpleNamespace]):
        self._responses = list(responses)
        self.queries: list[FakeBandiQuery] = []

    def table(self, name):
        query = FakeBandiQuery(name, self._responses.pop(0))
        self.queries.append(query)
        return query


class TestFetchBandi:
    async def test_page_of_open_still_counts_closed_in_total(self):
        secondary = FakeSecondary([
            SimpleNamespace(data=[bando_row(1), bando_row(2)], count=5),
            SimpleNamespace(data=[bando_row(99)], count=7),
        ])
        page = await fetch_bandi(secondary, BandiFilters(), 1, 2, "pubblicazione_desc")
        open_q, closed_q = secondary.queries
        assert (open_q.name, closed_q.name) == ("bando_pubblico", "bando_pubblico")
        assert [item.id for item in page.items] == [1, 2]
        assert page.total == 12
        assert open_q.range_args == (0, 1)
        # i conteggi guidano offset del segmento chiusi e totale:
        # entrambe le query DEVONO chiederli esatti
        assert open_q.select_kwargs == {"count": "exact"}
        assert closed_q.select_kwargs == {"count": "exact"}
        # pagina già piena: dei chiusi serve solo il conteggio
        assert closed_q.limit_arg == 1
        assert closed_q.range_args is None

    async def test_page_straddling_boundary_merges_the_two_tails(self):
        # 4 non chiusi, pagina 2 da 3 (offset 3): 1 non chiuso + 2 chiusi.
        secondary = FakeSecondary([
            SimpleNamespace(data=[bando_row(4)], count=4),
            SimpleNamespace(data=[bando_row(101), bando_row(102)], count=6),
        ])
        page = await fetch_bandi(secondary, BandiFilters(), 2, 3, "pubblicazione_desc")
        _, closed_q = secondary.queries
        assert [item.id for item in page.items] == [4, 101, 102]
        assert page.total == 10
        # il segmento dei chiusi riparte dal proprio inizio
        assert closed_q.range_args == (0, 1)

    async def test_page_fully_inside_closed_tier_offsets_into_it(self):
        # 3 non chiusi, pagina 3 da 2 (offset 4): tutta nel segmento chiusi.
        secondary = FakeSecondary([
            SimpleNamespace(data=[], count=3),
            SimpleNamespace(data=[bando_row(103), bando_row(104)], count=6),
        ])
        page = await fetch_bandi(secondary, BandiFilters(), 3, 2, "scadenza_asc")
        _, closed_q = secondary.queries
        assert [item.id for item in page.items] == [103, 104]
        assert page.total == 9
        assert closed_q.range_args == (1, 2)

    async def test_scadenza_asc_flips_direction_for_closed_tier(self):
        # tra i non chiusi la scadenza più vicina, tra i chiusi la chiusura
        # più recente (non i bandi scaduti da più tempo)
        secondary = FakeSecondary([
            SimpleNamespace(data=[bando_row(1)], count=1),
            SimpleNamespace(data=[bando_row(2)], count=1),
        ])
        await fetch_bandi(secondary, BandiFilters(), 1, 2, "scadenza_asc")
        open_q, closed_q = secondary.queries
        # nullsfirst=False: i bandi senza scadenza in fondo al proprio segmento
        assert open_q.orders == [("data_scadenza", False, False), ("id", False, None)]
        assert closed_q.orders == [("data_scadenza", True, False), ("id", False, None)]

    async def test_tier_filters_are_applied_to_both_queries(self):
        secondary = FakeSecondary([
            SimpleNamespace(data=[], count=0),
            SimpleNamespace(data=[], count=0),
        ])
        await fetch_bandi(secondary, BandiFilters(), 1, 20, "pubblicazione_desc")
        open_q, closed_q = secondary.queries
        assert [f"({f})" for f in open_q.or_filters] == [SEGMENTO_APERTI]
        assert closed_q.or_filters == []
        assert closed_q.eq_filters == [("stato_effettivo", "chiuso")]
        for query in (open_q, closed_q):
            assert "stato_processing" not in [c for c, _ in query.eq_filters]

    async def test_fts_is_applied_to_both_queries(self):
        secondary = FakeSecondary([
            SimpleNamespace(data=[], count=0),
            SimpleNamespace(data=[], count=0),
        ])
        await fetch_bandi(secondary, BandiFilters(q="energia"), 1, 20, "pubblicazione_desc")
        for query in secondary.queries:
            assert query.filters == [("ricerca", "wfts(italian)", "energia")]
        open_q, closed_q = secondary.queries
        assert [f"({f})" for f in open_q.or_filters] == [SEGMENTO_APERTI]
        assert closed_q.eq_filters == [("stato_effettivo", "chiuso")]

    async def test_stato_effettivo_nelle_card(self):
        secondary = FakeSecondary([
            SimpleNamespace(data=[{**bando_row(1), "stato_effettivo": "aperto"}], count=1),
            SimpleNamespace(data=[{**bando_row(2), "stato_effettivo": "chiuso"}], count=1),
        ])
        page = await fetch_bandi(secondary, BandiFilters(), 1, 5, "pubblicazione_desc")
        assert [(i.stato_bando, i.stato_effettivo) for i in page.items] == [
            ("aperto", "aperto"), ("aperto", "chiuso")
        ]

    async def test_unknown_sort_falls_back_to_most_recent(self):
        secondary = FakeSecondary([
            SimpleNamespace(data=[], count=0),
            SimpleNamespace(data=[], count=0),
        ])
        page = await fetch_bandi(secondary, BandiFilters(), 1, 20, "boh")
        open_q, _ = secondary.queries
        assert open_q.orders[0] == ("data_pubblicazione", True, False)
        assert page.total == 0
        assert page.total_pages == 0

    async def test_row_flipping_tier_between_queries_is_deduplicated(self):
        # Le due query non condividono uno snapshot: un bando che diventa
        # chiuso tra l'una e l'altra comparirebbe in entrambe le code.
        secondary = FakeSecondary([
            SimpleNamespace(data=[bando_row(1), bando_row(2)], count=2),
            SimpleNamespace(data=[bando_row(2), bando_row(50)], count=4),
        ])
        page = await fetch_bandi(secondary, BandiFilters(), 1, 4, "pubblicazione_desc")
        assert [item.id for item in page.items] == [1, 2, 50]


class TestCandidatiAlert:
    async def test_candidati_ereditano_il_segmento_aperti(self, client):
        # Gli alert riusano apply_open_tier: sospesi, revocati e stati non
        # previsti non diventano mai candidati (contratto DB bandi §4).
        secondary = FakeSecondary([SimpleNamespace(data=[], count=None)])
        await carica_candidati(
            secondary,
            oggi=TODAY,
            attivazione=date(2026, 6, 1),
            orizzonte_giorni=60,
            fuso=ZoneInfo("Europe/Rome"),
        )
        [query] = secondary.queries
        segmento_aperti = params_of(
            apply_open_tier(client.from_("bando_pubblico").select("id"), TODAY)
        )
        assert segmento_aperti["or"] == [SEGMENTO_APERTI]
        assert f"({query.or_filters[0]})" == SEGMENTO_APERTI


def filters_client() -> httpx.AsyncClient:
    """App minima con la sola dipendenza parse_filters e gli handler degli errori
    di produzione (un BadRequestError diventa 400 come in /bandi)."""
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/filtri")
    async def filtri(filters: Annotated[BandiFilters, Depends(parse_filters)]):
        return {"stato": filters.stato}

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


class TestFiltroStato:
    """Il filtro ``stato`` non risponde mai 400 per un valore sconosciuto
    (contratto DB bandi §7, R0-a): lo ignora."""

    @pytest.mark.parametrize(
        ("stato", "atteso"),
        [
            ("pippo", []),
            ("sospeso", ["sospeso"]),
            ("revocato", ["revocato"]),
            ("aperto,pippo", ["aperto"]),
            ("aperto, chiuso", ["aperto", "chiuso"]),  # spazi intorno: valore noto
            ("aperto,in apertura prossimamente,chiuso", ["aperto", "in apertura prossimamente", "chiuso"]),
        ],
    )
    async def test_stati(self, stato, atteso):
        async with filters_client() as http:
            resp = await http.get("/filtri", params={"stato": stato})
        assert resp.status_code == 200
        assert resp.json() == {"stato": atteso}

    async def test_livello_non_valido_resta_400(self):
        async with filters_client() as http:
            resp = await http.get("/filtri", params={"livello": "boh"})
        assert resp.status_code == 400


class TestMapping:
    ROW = {
        "id": 17774,
        "slug": "lombardia-iniziativa-milo",
        "titolo": "Contributi Regione Lombardia",
        "titolo_breve": "Bando regionale Lombardia",
        "descrizione_breve": "Contributi a fondo perduto",
        "stato_bando": "aperto",
        "stato_effettivo": "chiuso",
        "livello": "flash_bando",
        "data_pubblicazione": "2026-05-26",
        "data_apertura": None,
        "data_scadenza": "2026-06-29",
        "importo_totale_eur": 1_000_000,
        "importo_max_per_progetto_eur": None,
        "ente_erogatore": "Regione Lombardia",
        "tipologie_bando": {"id": 3, "nome": "Bandi regionali / locali"},
        "modalita_erogazione": {"id": 1, "nome": "Fondo perduto"},
        "bando_regioni": [{"regioni": {"id": 10, "nome": "Lombardia"}}],
        # embed di filtro aliasato: va ignorato dal mapping
        "f_reg": [{"regione_id": 10}],
    }

    def test_map_list_item(self):
        item = map_list_item(self.ROW)
        assert item.slug == "lombardia-iniziativa-milo"
        assert item.stato_bando == "aperto"
        assert item.stato_effettivo == "chiuso"
        assert item.tipologia.nome == "Bandi regionali / locali"
        assert [r.nome for r in item.regioni] == ["Lombardia"]

    def test_map_detail_flattens_all_junctions(self):
        row = {
            **self.ROW,
            "area_geografica": "Lombardia",
            "tematica": ["Smart cities"],
            "link_bando": "https://example.com/bando",
            "link_candidatura": None,
            "contenuto": {"sections": []},
            "allegati": [],
            "programmi": {"id": 4, "nome": "PNRR"},
            "bando_settori": [{"settori": {"id": 1, "nome": "Trasporti"}}],
            "bando_beneficiari": [{"beneficiari": {"id": 2, "nome": "PMI"}}],
            "bando_codici_ateco": [
                {"codici_ateco": {"id": 3, "codice": "49", "descrizione": "Trasporto terrestre"}}
            ],
        }
        detail = map_detail(row)
        assert detail.programma.nome == "PNRR"
        assert [s.nome for s in detail.settori] == ["Trasporti"]
        assert [b.nome for b in detail.beneficiari] == ["PMI"]
        assert detail.codici_ateco[0].codice == "49"
        assert detail.tematica == ["Smart cities"]
        assert detail.cta.url == "https://example.com/bando"
        assert detail.cta.origine == "link_bando"

    def test_map_handles_missing_embeds(self):
        row = {**self.ROW, "tipologie_bando": None, "bando_regioni": []}
        item = map_list_item(row)
        assert item.tipologia is None
        assert item.regioni == []

    def test_map_detail_decodes_double_encoded_contenuto(self):
        # 5 righe reali del DB hanno contenuto come stringa JSON doppio-encodata.
        row = {**self.ROW, "contenuto": '{"sections": [{"type": "h2", "text": "Chi"}]}'}
        detail = map_detail(row)
        assert isinstance(detail.contenuto, dict)
        assert detail.contenuto["sections"][0]["text"] == "Chi"

    def test_map_detail_contenuto_object_passthrough(self):
        row = {**self.ROW, "contenuto": {"sections": []}}
        assert map_detail(row).contenuto == {"sections": []}


class TestNormalizeContenuto:
    def test_none(self):
        assert normalize_contenuto(None) is None

    def test_dict_passthrough(self):
        assert normalize_contenuto({"sections": [1]}) == {"sections": [1]}

    def test_json_string(self):
        assert normalize_contenuto('{"sections": []}') == {"sections": []}

    def test_invalid_string_becomes_none(self):
        assert normalize_contenuto("non è json") is None

    def test_json_non_object_becomes_none(self):
        assert normalize_contenuto("[1, 2, 3]") is None
