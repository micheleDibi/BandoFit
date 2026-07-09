"""Punteggio di compatibilità a-priori azienda↔bando (funzioni pure)."""

import time
from types import SimpleNamespace

from app.services import compatibility
from app.services.bandi_service import BandiFilters, bando_facet_ids, build_list_select
from app.services.compatibility import (
    build_company_facets,
    compute_compatibilita,
    invalidate_company_facets,
)
from app.services.openapi_mapping import all_regioni_ids, company_regioni_ids


def lookups(**overrides) -> SimpleNamespace:
    base = dict(
        codici_ateco=[
            SimpleNamespace(id=620, codice="62", descrizione="Software"),
            SimpleNamespace(id=630, codice="63", descrizione="Servizi info"),
            SimpleNamespace(id=850, codice="85", descrizione="Istruzione"),
        ],
        regioni=[
            SimpleNamespace(id=12, nome="Lazio"),
            SimpleNamespace(id=3, nome="Lombardia"),
            SimpleNamespace(id=15, nome="Puglia"),
        ],
        settori=[], beneficiari=[], tipologie=[], modalita=[], programmi=[],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestBuildCompanyFacets:
    def test_ateco_primario_e_secondari_e_multisede(self):
        # I beneficiari sono DICHIARATI sul profilo, non dedotti dalla visura.
        company = {
            "ateco_id": 620, "regione_id": 12, "settore_id": 7,
            "beneficiari": [{"id": 2, "nome": "PMI"}],
        }
        derived = {
            "ateco_divisione": "62",
            "ateco_secondari": ["63.01", "85.2"],
            "regioni_ids": [12, 15],
        }
        facets = build_company_facets(company, derived, lookups())
        assert facets.ateco_ids == {620, 630, 850}
        assert facets.regioni_ids == {12, 15}
        assert facets.settore_id == 7
        assert facets.beneficiari_ids == {2}
        assert facets.sufficiente is True

    def test_non_sufficiente_senza_ateco(self):
        facets = build_company_facets({"regione_id": 12}, {}, lookups())
        assert facets.sufficiente is False

    def test_non_sufficiente_senza_regione(self):
        facets = build_company_facets({"ateco_id": 620}, {}, lookups())
        assert facets.sufficiente is False

    def test_fallback_sede_legale_senza_regioni_ids(self):
        # Azienda importata prima della modifica: nessun regioni_ids → sede legale.
        facets = build_company_facets({"ateco_id": 620, "regione_id": 12}, {}, lookups())
        assert facets.regioni_ids == {12}

    def test_beneficiari_non_dichiarati(self):
        # Campo vuoto: nessun id → il requisito non entrerà nel punteggio.
        facets = build_company_facets({"ateco_id": 620, "regione_id": 12}, {}, lookups())
        assert facets.beneficiari_ids == set()


def _facets(**over):
    base = dict(regioni_ids={12}, ateco_ids={620}, settore_id=7, beneficiari_ids={2}, sufficiente=True)
    base.update(over)
    return SimpleNamespace(**base)


class TestComputeCompatibilita:
    def test_una_voce_in_comune_soddisfa_il_requisito(self):
        # Le voci di un requisito sono ALTERNATIVE: il bando su 3 settori non
        # chiede di operare in tutti e 3. Un solo settore in comune → soddisfatto.
        bando = {"regioni": [12, 3], "ateco": [620, 630], "settori": [7, 8, 9], "beneficiari": [2, 5]}
        out = compute_compatibilita(_facets(), bando, totale_regioni=3)
        assert out["matched"] == 4  # 4 requisiti su 4, non 4 voci su 9
        assert out["totale"] == 4
        assert out["punteggio"] == 100
        assert out["dimensioni"]["settori"] == {
            "soddisfatta": True, "matched": 1, "totale": 3, "matched_ids": [7], "nazionale": False,
        }
        assert out["dimensioni"]["ateco"]["matched_ids"] == [620]

    def test_requisito_non_soddisfatto_pesa_come_gli_altri(self):
        # 3 requisiti su 4: beneficiari senza nessuna voce in comune.
        bando = {"regioni": [12], "ateco": [620], "settori": [7, 8], "beneficiari": [5, 9]}
        out = compute_compatibilita(_facets(), bando, totale_regioni=3)
        assert out["matched"] == 3 and out["totale"] == 4
        assert out["punteggio"] == 75
        assert out["dimensioni"]["beneficiari"]["soddisfatta"] is False
        assert out["dimensioni"]["beneficiari"]["matched_ids"] == []

    def test_bando_nazionale(self):
        # Bando che copre tutte le regioni del catalogo: il territorio è
        # soddisfatto da sé. `matched_ids` resta l'intersezione vera — le
        # regioni dove l'azienda ha davvero una sede.
        bando = {"regioni": [12, 3, 15], "ateco": [620]}
        out = compute_compatibilita(_facets(regioni_ids={12}), bando, totale_regioni=3)
        assert out["dimensioni"]["regioni"] == {
            "soddisfatta": True, "matched": 1, "totale": 3, "matched_ids": [12], "nazionale": True,
        }
        assert out["matched"] == 2 and out["totale"] == 2
        assert out["punteggio"] == 100

    def test_gate_azienda_non_sufficiente(self):
        bando = {"regioni": [12], "ateco": [620]}
        assert compute_compatibilita(_facets(sufficiente=False), bando, totale_regioni=3) is None
        assert compute_compatibilita(None, bando, totale_regioni=3) is None

    def test_settore_escluso_se_azienda_senza(self):
        # Azienda senza settore: il requisito non entra nel denominatore.
        bando = {"regioni": [12], "settori": [8, 9]}
        out = compute_compatibilita(_facets(settore_id=None), bando, totale_regioni=3)
        assert "settori" not in out["dimensioni"]
        assert out["totale"] == 1  # solo regioni

    def test_beneficiari_esclusi_se_azienda_senza(self):
        bando = {"regioni": [12], "beneficiari": [2, 5]}
        out = compute_compatibilita(_facets(beneficiari_ids=set()), bando, totale_regioni=3)
        assert "beneficiari" not in out["dimensioni"]
        assert out["totale"] == 1

    def test_nessuna_dimensione_valutabile_ritorna_none(self):
        # Bando che vincola solo dimensioni che l'azienda non ha.
        bando = {"settori": [8]}
        assert compute_compatibilita(_facets(settore_id=None), bando, totale_regioni=3) is None

    def test_zero_in_comune(self):
        bando = {"regioni": [3], "ateco": [850]}
        out = compute_compatibilita(_facets(regioni_ids={12}, ateco_ids={620}), bando, totale_regioni=3)
        assert out["matched"] == 0 and out["totale"] == 2 and out["punteggio"] == 0
        assert out["dimensioni"]["regioni"]["soddisfatta"] is False
        assert out["dimensioni"]["regioni"]["matched_ids"] == []

    def test_regione_soddisfatta_da_una_sede_secondaria(self):
        # Bando su 2 regioni su 3 del catalogo: NON nazionale. La sede legale è
        # fuori, ma un'unità locale è in una regione ammessa → soddisfatto.
        bando = {"regioni": [12, 3]}
        out = compute_compatibilita(_facets(regioni_ids={15, 3}), bando, totale_regioni=3)
        dim = out["dimensioni"]["regioni"]
        assert dim["nazionale"] is False
        assert dim["soddisfatta"] is True
        assert dim["matched"] == len(dim["matched_ids"]) == 1
        assert dim["matched_ids"] == [3]


class TestCompanyRegioniIds:
    def test_multisede_da_derived(self):
        assert company_regioni_ids({"regione_id": 12}, {"regioni_ids": [12, 15, 3]}) == {12, 15, 3}

    def test_fallback_sede_legale(self):
        assert company_regioni_ids({"regione_id": 12}, {}) == {12}
        assert company_regioni_ids({}, {"regione_id": 7}) == {7}

    def test_vuoto(self):
        assert company_regioni_ids({}, {}) == set()


class TestAllRegioniIds:
    def test_sede_legale_piu_unita_locali(self):
        payload = {
            "address": {"region": {"description": "LAZIO"}},
            "allOffices": [
                {"address": {"region": {"description": "PUGLIA"}}},
                {"address": {"region": {"description": "LAZIO"}}},  # duplicato
                {"address": {"region": {"description": "SVIZZERA"}}},  # non mappabile
            ],
        }
        assert all_regioni_ids(payload, lookups()) == [12, 15]


class TestBandoFacetIds:
    def test_shape_elenco_id_only(self):
        row = {
            "bando_regioni": [{"regioni": {"id": 12, "nome": "Lazio"}}],
            "bando_settori": [{"settore_id": 7}, {"settore_id": 8}],
            "bando_beneficiari": [{"beneficiario_id": 2}],
            "bando_codici_ateco": [{"codice_ateco_id": 620}],
        }
        ids = bando_facet_ids(row)
        assert ids == {"regioni": [12], "ateco": [620], "settori": [7, 8], "beneficiari": [2]}

    def test_shape_dettaglio_annidato(self):
        row = {
            "bando_regioni": [{"regioni": {"id": 12, "nome": "Lazio"}}],
            "bando_settori": [{"settori": {"id": 7, "nome": "ICT"}}],
            "bando_beneficiari": [{"beneficiari": {"id": 2, "nome": "PMI"}}],
            "bando_codici_ateco": [{"codici_ateco": {"id": 620, "codice": "62"}}],
        }
        ids = bando_facet_ids(row)
        assert ids == {"regioni": [12], "ateco": [620], "settori": [7], "beneficiari": [2]}


class TestSelectScoring:
    def test_senza_punteggio_niente_embed_extra(self):
        select = build_list_select(BandiFilters())
        assert "bando_settori" not in select and "bando_codici_ateco" not in select

    def test_con_punteggio_embed_id_only(self):
        select = build_list_select(BandiFilters(), include_facets=True)
        assert "bando_settori(settore_id)" in select
        assert "bando_beneficiari(beneficiario_id)" in select
        assert "bando_codici_ateco(codice_ateco_id)" in select

    def test_alias_del_filtro_convive_con_embed_di_scoring(self):
        select = build_list_select(BandiFilters(settori=[1]), include_facets=True)
        assert "f_set:bando_settori!inner(settore_id)" in select  # alias per il filtro
        assert "bando_settori(settore_id)" in select  # embed plain per il punteggio


class TestCacheFacets:
    def test_invalidate_rimuove_la_voce(self):
        compatibility._cache["owner-1"] = (None, time.monotonic())
        invalidate_company_facets("owner-1")
        assert "owner-1" not in compatibility._cache

    def test_invalidate_normalizza_la_chiave(self):
        # gli owner_id in cache sono stringhe (str(uuid)): invalidare con un
        # non-str non deve mancare la voce.
        compatibility._cache["42"] = (None, time.monotonic())
        invalidate_company_facets(42)
        assert "42" not in compatibility._cache

    def test_invalidate_su_chiave_assente_non_esplode(self):
        invalidate_company_facets("mai-vista")


async def test_get_company_facets_degrada_a_none_su_errore(monkeypatch):
    """Il badge è accessorio: un errore sui dati aziendali non deve far
    fallire l'elenco/dettaglio bandi (che vivono sul DB secondario)."""

    async def boom(*_args, **_kwargs):
        raise RuntimeError("primary irraggiungibile")

    monkeypatch.setattr(compatibility, "_load_company_facets", boom)
    assert await compatibility.get_company_facets(object(), {"id": "u1"}, lookups()) is None
