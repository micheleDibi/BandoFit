"""`GET /lookups`: in salute la risposta si tiene un'ora nel browser; in
degrado (catalogo non leggibile: cache scaduta o liste vuote) `no-store`,
perché il server ritenta dopo un minuto."""

import httpx
import pytest
from fastapi import FastAPI

from app.api import deps
from app.api.routers import lookups as lookups_router
from app.core.errors import register_exception_handlers
from app.services import lookup_service as ls
from tests.test_lookup_service import FakeSecondary, cache_pulita, cache_scaduta, errore

__all__ = ["cache_pulita"]  # fixture autouse importata: cache azzerata a ogni test


def _client(secondary) -> httpx.AsyncClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(lookups_router.router, prefix="/api/v1")
    app.dependency_overrides[deps.get_current_user] = lambda: {"id": "u1"}
    app.dependency_overrides[deps.get_secondary] = lambda: secondary
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _get(secondary) -> httpx.Response:
    async with _client(secondary) as client:
        return await client.get("/api/v1/lookups")


async def test_in_salute_cache_di_un_ora():
    risposta = await _get(FakeSecondary())
    assert risposta.status_code == 200
    assert risposta.headers["cache-control"] == "private, max-age=3600"
    assert risposta.json()["regioni"] == [{"id": 1, "nome": "regioni"}]
    assert ls.in_degrado() is False


async def test_cache_vuota_liste_vuote_no_store():
    risposta = await _get(FakeSecondary(guasto=errore("42703")))
    assert risposta.status_code == 200
    assert risposta.headers["cache-control"] == "no-store"
    assert risposta.json()["regioni"] == []
    assert ls.in_degrado() is True


async def test_cache_scaduta_servita_no_store(monkeypatch):
    vecchi = await ls.get_lookups(FakeSecondary())
    cache_scaduta(monkeypatch, vecchi)
    risposta = await _get(FakeSecondary(guasto=errore("42501")))
    assert risposta.status_code == 200
    assert risposta.headers["cache-control"] == "no-store"
    assert risposta.json()["regioni"] == [{"id": 1, "nome": "regioni"}]


async def test_dopo_la_ripresa_torna_la_cache_lunga(monkeypatch):
    await _get(FakeSecondary(guasto=errore("42703")))
    monkeypatch.setattr(ls, "_errore_at", ls._errore_at - ls._RINVIO_ERRORE_SECONDS - 1)
    risposta = await _get(FakeSecondary())
    assert risposta.headers["cache-control"] == "private, max-age=3600"
    assert risposta.json()["regioni"] == [{"id": 1, "nome": "regioni"}]


@pytest.mark.parametrize("codice", ["42703", "PGRST205"])
async def test_nessun_5xx_in_degrado(codice):
    assert (await _get(FakeSecondary(guasto=errore(codice)))).status_code == 200


# ------------------------------------------- facet dell'azienda in degrado


class _Spia:
    """`get_company_facets` / `load_company_facets` finti: contano le chiamate."""

    def __init__(self, esito):
        self.esito = esito
        self.chiamate = 0

    async def __call__(self, _primary, _active, _lookups):
        self.chiamate += 1
        return self.esito


def _app_bandi(monkeypatch, secondary, spia: _Spia) -> tuple[httpx.AsyncClient, dict]:
    """Rotte dei bandi con i lookup veri (degrado compreso) e il catalogo
    finto: si cattura cosa arriva a elenco e dettaglio."""
    from app.api.deps import ActiveCompany
    from app.api.routers import bandi as bandi_router
    from app.schemas.bando import BandoDetail
    from app.schemas.common import Page
    from app.services import bandi_service

    visti: dict = {}

    async def fetch_bandi(_secondary, _filters, page, page_size, _sort, **kwargs):
        visti.update(kwargs)
        return Page.build([], 0, page, page_size)

    async def fetch_bando_by_slug(_secondary, slug, **kwargs):
        visti.update(kwargs)
        return BandoDetail(id=1, slug=slug)

    monkeypatch.setattr(bandi_service, "fetch_bandi", fetch_bandi)
    monkeypatch.setattr(bandi_service, "fetch_bando_by_slug", fetch_bando_by_slug)
    monkeypatch.setattr(bandi_router, "get_company_facets", spia)
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(bandi_router.router, prefix="/api/v1")
    app.dependency_overrides[deps.get_current_user] = lambda: {"id": "u1"}
    app.dependency_overrides[deps.active_company] = lambda: ActiveCompany(
        company_id="c1", owner_id="u1", editable=True, is_multi=False
    )
    app.dependency_overrides[deps.get_primary] = lambda: object()
    app.dependency_overrides[deps.get_secondary] = lambda: secondary
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    return client, visti


@pytest.mark.parametrize("percorso", ["/api/v1/bandi", "/api/v1/bandi/un-bando"])
async def test_bandi_in_degrado_senza_cache_nessun_badge(monkeypatch, percorso):
    # Liste vuote: i facet perderebbero le divisioni ATECO secondarie e il
    # badge uscirebbe sottostimato. Nessun facet, nemmeno letto.
    spia = _Spia(esito="facet")
    client, visti = _app_bandi(monkeypatch, FakeSecondary(guasto=errore("42703")), spia)
    async with client:
        risposta = await client.get(percorso)
    assert risposta.status_code == 200
    assert spia.chiamate == 0
    assert visti["company_facets"] is None and visti["totale_regioni"] == 0


@pytest.mark.parametrize("percorso", ["/api/v1/bandi", "/api/v1/bandi/un-bando"])
async def test_bandi_con_cache_scaduta_badge_calcolato(monkeypatch, percorso):
    vecchi = await ls.get_lookups(FakeSecondary())
    cache_scaduta(monkeypatch, vecchi)
    spia = _Spia(esito="facet")
    client, visti = _app_bandi(monkeypatch, FakeSecondary(guasto=errore("42501")), spia)
    async with client:
        risposta = await client.get(percorso)
    assert risposta.status_code == 200
    assert spia.chiamate == 1
    assert visti["company_facets"] == "facet" and visti["totale_regioni"] == 1


def _app_facets(primary, secondary) -> httpx.AsyncClient:
    from app.api.deps import ActiveCompany
    from app.api.routers import company as company_router

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(company_router.router, prefix="/api/v1")
    app.dependency_overrides[deps.get_current_user] = lambda: {"id": "u1"}
    app.dependency_overrides[deps.active_company] = lambda: ActiveCompany(
        company_id="c1", owner_id="u1", editable=True, is_multi=False
    )
    app.dependency_overrides[deps.get_primary] = lambda: primary
    app.dependency_overrides[deps.get_secondary] = lambda: secondary
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_facets_azienda_in_degrado_vuoti_e_fuori_cache():
    from app.services import compatibility
    from tests.test_lookup_service import PrimarioIntoccabile

    compatibility.invalidate_company_facets("c1")
    async with _app_facets(PrimarioIntoccabile(), FakeSecondary(guasto=errore("42703"))) as c:
        risposta = await c.get("/api/v1/me/company/facets")
    assert risposta.status_code == 200
    assert risposta.json() == {
        "regioni": [], "ateco": [], "settori": [], "beneficiari": [], "sufficiente": False
    }
    # facet parziali mai in cache: alla ripresa il badge li rilegge subito
    assert "c1" not in compatibility._cache


async def test_facets_azienda_con_cache_scaduta_calcolati(monkeypatch):
    from app.services import compatibility
    from tests.test_compatibility import _primary

    vecchi = await ls.get_lookups(FakeSecondary())
    cache_scaduta(monkeypatch, vecchi)
    compatibility.invalidate_company_facets("c1")
    primary = _primary({"ateco_id": 620, "regione_id": 12, "settore_id": None, "beneficiari": []},
                       {"ateco_secondari": ["62.01"], "regioni_ids": [12, 15]})
    try:
        async with _app_facets(primary, FakeSecondary(guasto=errore("42501"))) as c:
            risposta = await c.get("/api/v1/me/company/facets")
    finally:
        compatibility.invalidate_company_facets("c1")
    assert risposta.status_code == 200
    # la divisione secondaria «62» si mappa con la cache scaduta (id 1)
    assert risposta.json() == {
        "regioni": [12, 15], "ateco": [1, 620], "settori": [], "beneficiari": [],
        "sufficiente": True,
    }
