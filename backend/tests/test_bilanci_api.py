"""API dei bilanci (WP1): GET /me/company/bilanci e POST .../bilanci/recupera.

Mini-app con il solo router aziendale e `dependency_overrides` (modello di
test_company_pdf.py); dietro, il servizio vero sul primario finto a stato di
test_bilanci_service. Si verificano la forma JSON del contratto, i permessi
(il membro legge, non spende) e il formato degli errori {error: {code}}."""

import httpx
import pytest
from fastapi import FastAPI

from app.api import deps
from app.api.deps import ActiveCompany
from app.api.routers import company
from app.core.errors import OpenapiTimeoutError, register_exception_handlers
from tests.test_bilanci_service import (  # noqa: F401  stub_settings: fixture autouse
    COMPANY,
    OWNER,
    PIVA,
    _iso,
    db_base,
    fake_openapi,
    it_advanced_dato,
    stub_settings,
)

CAMPI_ESERCIZIO = {
    "anno", "data_chiusura", "tipo_bilancio", "fatturato", "valore_produzione",
    "risultato_esercizio", "patrimonio_netto", "capitale_sociale", "totale_attivo",
    "debiti_totali", "disponibilita_liquide", "ebitda", "ebit", "cash_flow",
    "oneri_finanziari", "dipendenti", "costo_personale", "retribuzione_media_lorda", "fonti",
}


def _client(db, openapi=None, *, editable: bool = True) -> httpx.AsyncClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(company.router, prefix="/api/v1")
    app.dependency_overrides[deps.get_current_user] = lambda: {"id": OWNER}
    app.dependency_overrides[deps.active_company] = lambda: ActiveCompany(
        company_id=COMPANY, owner_id=OWNER, editable=editable
    )
    app.dependency_overrides[deps.get_primary] = lambda: db
    app.dependency_overrides[deps.get_secondary] = lambda: object()
    app.dependency_overrides[deps.get_openapi] = lambda: openapi or fake_openapi()
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


class TestGet:
    async def test_forma_del_contratto(self):
        db = db_base(company_financials_stato=[{
            "company_profile_id": COMPANY, "advanced_esito": "ok", "advanced_motivo": None,
            "advanced_tentato_at": _iso(600), "advanced_fetched_at": _iso(600),
            "advanced_raw": it_advanced_dato(), "advanced_sandbox": False,
            "advanced_fetch_count": 1, "mapping_versione": 0,
        }])
        async with _client(db) as client:
            resp = await client.get("/api/v1/me/company/bilanci")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {
            "editable", "stato", "motivo", "storico_esito", "ultimo_tentativo_at",
            "recuperabile_da", "sandbox", "esercizi", "indicatori", "fasce",
        }
        assert body["stato"] == "disponibili" and body["storico_esito"] == "ok"
        anni = [e["anno"] for e in body["esercizi"]]
        assert anni == sorted(anni) == [2017, 2018, 2019, 2020, 2021, 2022]
        assert set(body["esercizi"][0]) == CAMPI_ESERCIZIO
        assert isinstance(body["esercizi"][-1]["fatturato"], float)  # numeri JSON
        assert set(body["indicatori"][0]) == {
            "chiave", "etichetta", "valore", "unita", "anni", "formula", "motivo_mancanza",
        }
        assert set(body["fasce"]) == {
            "fatturato", "patrimonio_netto", "dipendenti", "trend_fatturato", "anno_riferimento",
        }
        # i payload grezzi non escono mai
        assert "advanced_raw" not in resp.text and "balanceSheets" not in resp.text
        assert PIVA not in resp.text

    async def test_membro_legge_in_sola_lettura(self):
        async with _client(db_base(), editable=False) as client:
            resp = await client.get("/api/v1/me/company/bilanci")
        assert resp.status_code == 200
        assert resp.json()["editable"] is False
        assert resp.json()["recuperabile_da"] is None


class TestRecupera:
    async def test_titolare_200(self):
        db = db_base()
        openapi = fake_openapi()
        async with _client(db, openapi) as client:
            resp = await client.post("/api/v1/me/company/bilanci/recupera")
        assert resp.status_code == 200
        assert resp.json()["storico_esito"] == "ok"
        assert len(openapi.calls) == 1

    async def test_membro_attivo_403(self):
        db = db_base()
        openapi = fake_openapi()
        async with _client(db, openapi, editable=False) as client:
            resp = await client.post("/api/v1/me/company/bilanci/recupera")
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden"
        assert openapi.calls == [] and db.rpcs == []

    @pytest.mark.parametrize(
        ("prepara", "status", "code"),
        [
            (lambda db, o: setattr(o, "enabled", False), 503, "openapi_not_configured"),
            (lambda db, o: setattr(db, "quota", False), 429, "limite_giornaliero_openapi"),
            (lambda db, o: setattr(db, "lock", False), 409, "import_in_progress"),
            (
                lambda db, o: db.tabelle.__setitem__("company_financials_stato", [{
                    "company_profile_id": COMPANY, "advanced_tentato_at": _iso(1),
                    "mapping_versione": 1,
                }]),
                409,
                "bilanci_cooldown",
            ),
            (
                lambda db, o: db.tabelle["company_data"][0]["raw"]["legalForm"]["legalForm"]
                .update(code="SP"),
                409,
                "bilanci_non_previsti",
            ),
        ],
        ids=["non_configurato", "quota", "lock", "cooldown", "societa_di_persone"],
    )
    async def test_errori_formato_error_code(self, prepara, status, code):
        db = db_base()
        openapi = fake_openapi()
        prepara(db, openapi)
        async with _client(db, openapi) as client:
            resp = await client.post("/api/v1/me/company/bilanci/recupera")
        assert resp.status_code == status
        assert resp.json()["error"]["code"] == code
        assert resp.json()["error"]["message"]
        assert openapi.calls == []

    async def test_timeout_504(self):
        openapi = fake_openapi(errore=OpenapiTimeoutError())
        async with _client(db_base(), openapi) as client:
            resp = await client.post("/api/v1/me/company/bilanci/recupera")
        assert resp.status_code == 504
        assert resp.json()["error"]["code"] == "openapi_timeout"

    async def test_errore_provider_502(self):
        from app.clients.openapi import OpenapiNonInviataError

        openapi = fake_openapi(errore=OpenapiNonInviataError())
        async with _client(db_base(), openapi) as client:
            resp = await client.post("/api/v1/me/company/bilanci/recupera")
        assert resp.status_code == 502
        assert resp.json()["error"]["code"] == "openapi_error"
