"""Test del builder dei filtri PostgREST per l'elenco bandi.

Usano il query builder reale di postgrest-py SENZA rete: si costruisce la
query e si ispezionano i parametri URL generati.
"""

from datetime import date
from types import SimpleNamespace

import pytest
from postgrest import AsyncPostgrestClient

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
    query = client.from_("bando").select(build_list_select(filters))
    return apply_filters(query, filters, today=TODAY)


class TestSelect:
    def test_base_select_has_display_embeds_only(self):
        select = build_list_select(BandiFilters())
        assert "bando_regioni(regioni(id,nome))" in select
        assert "!inner" not in select

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
    def test_always_filters_completed_with_slug(self, client):
        params = params_of(build(client, BandiFilters()))
        assert params["stato_processing"] == ["eq.completed"]
        assert params["slug"] == ["not.is.null"]

    def test_stato_in(self, client):
        params = params_of(build(client, BandiFilters(stato=["aperto", "in apertura prossimamente"])))
        [value] = params["stato_bando"]
        assert value.startswith("in.(")
        assert "aperto" in value and "in apertura prossimamente" in value

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
    def test_fts_uses_wfts_italian_on_all_columns(self, client):
        # Grezzi + rielaborati: l'utente cerca le parole che legge in card.
        params = params_of(build(client, BandiFilters(q="transizione digitale")))
        [value] = params["or"]
        assert value == (
            "(titolo_raw.wfts(italian).transizione digitale,"
            "descrizione_raw.wfts(italian).transizione digitale,"
            "titolo.wfts(italian).transizione digitale,"
            "titolo_breve.wfts(italian).transizione digitale,"
            "descrizione_breve.wfts(italian).transizione digitale)"
        )

    def test_sanitize_strips_grammar_breaking_chars(self):
        assert sanitize_fts_term("a,b(c)d\\e") == "a b c d e"
        assert sanitize_fts_term("  PNRR  ") == "PNRR"

    def test_sanitize_strips_double_quotes(self):
        # I doppi apici aprirebbero un token quotato mai chiuso in or=(...).
        assert '"' not in sanitize_fts_term('"bando energia" 2024')

    def test_blank_term_after_sanitize_adds_no_or(self, client):
        params = params_of(build(client, BandiFilters(q="(),")))
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


class TestTiers:
    """I due segmenti (non chiusi / chiusi) devono essere complementari e
    null-safe: la partizione è il contratto su cui poggia la paginazione."""

    def test_open_tier_excludes_chiusi_and_scaduti(self, client):
        params = params_of(apply_open_tier(build(client, BandiFilters()), TODAY))
        assert params["or"] == [
            "(stato_bando.neq.chiuso,stato_bando.is.null)",
            "(data_scadenza.gte.2026-07-03,data_scadenza.is.null)",
        ]

    def test_closed_tier_matches_stato_or_scadenza_passata(self, client):
        params = params_of(apply_closed_tier(build(client, BandiFilters()), TODAY))
        assert params["or"] == ["(stato_bando.eq.chiuso,data_scadenza.lt.2026-07-03)"]

    def test_tier_or_coexists_with_fts_or(self, client):
        # PostgREST mette in AND i parametri ``or`` ripetuti: la ricerca
        # full-text e il segmento devono restare condizioni separate.
        params = params_of(apply_open_tier(build(client, BandiFilters(q="energia")), TODAY))
        assert len(params["or"]) == 3
        assert params["or"][0].startswith("(titolo_raw.wfts")

    def test_today_italy_is_a_date(self):
        assert isinstance(today_italy(), date)


def bando_row(id_: int) -> dict:
    return {**TestMapping.ROW, "id": id_, "slug": f"bando-{id_}"}


class FakeBandiQuery:
    """Registra la catena di chiamate del builder e risponde con dati canned."""

    def __init__(self, response: SimpleNamespace):
        self._response = response
        self.select_kwargs: dict = {}
        self.or_filters: list[str] = []
        self.orders: list[tuple] = []
        self.range_args: tuple | None = None
        self.limit_arg: int | None = None

    def select(self, *args, **kwargs):
        self.select_kwargs = kwargs
        return self

    def eq(self, *args):
        return self

    @property
    def not_(self):
        return self

    def is_(self, *args):
        return self

    def in_(self, *args):
        return self

    def gte(self, *args):
        return self

    def lte(self, *args):
        return self

    def or_(self, filters: str):
        self.or_filters.append(filters)
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
        query = FakeBandiQuery(self._responses.pop(0))
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
        assert any("stato_bando.neq.chiuso" in f for f in open_q.or_filters)
        assert any("data_scadenza.gte." in f for f in open_q.or_filters)
        assert any("stato_bando.eq.chiuso" in f for f in closed_q.or_filters)

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


class TestMapping:
    ROW = {
        "id": 17774,
        "slug": "lombardia-iniziativa-milo",
        "titolo": "Contributi Regione Lombardia",
        "titolo_breve": "Bando regionale Lombardia",
        "descrizione_breve": "Contributi a fondo perduto",
        "stato_bando": "aperto",
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
        assert item.tipologia.nome == "Bandi regionali / locali"
        assert [r.nome for r in item.regioni] == ["Lombardia"]

    def test_map_detail_flattens_all_junctions(self):
        row = {
            **self.ROW,
            "area_geografica": "Lombardia",
            "tematica": ["Smart cities"],
            "link_bando": "https://example.com",
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
