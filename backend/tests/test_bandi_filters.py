"""Test del builder dei filtri PostgREST per l'elenco bandi.

Usano il query builder reale di postgrest-py SENZA rete: si costruisce la
query e si ispezionano i parametri URL generati.
"""

from datetime import date

import pytest
from postgrest import AsyncPostgrestClient

from app.services.bandi_service import (
    BandiFilters,
    apply_filters,
    build_list_select,
    map_detail,
    map_list_item,
    sanitize_fts_term,
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
    def test_fts_uses_wfts_italian_on_both_columns(self, client):
        params = params_of(build(client, BandiFilters(q="transizione digitale")))
        [value] = params["or"]
        assert value == (
            "(titolo_raw.wfts(italian).transizione digitale,"
            "descrizione_raw.wfts(italian).transizione digitale)"
        )

    def test_sanitize_strips_grammar_breaking_chars(self):
        assert sanitize_fts_term("a,b(c)d\\e") == "a b c d e"
        assert sanitize_fts_term("  PNRR  ") == "PNRR"

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
