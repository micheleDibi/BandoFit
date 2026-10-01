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
