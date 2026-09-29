"""API del consulto dalla call (WP9, W1): `GET /progettista/richieste/{id}/call`
(rotta aggiunta al router ESISTENTE del progettista, con il flag sulla sola
rotta) e `POST /partenariati/call/{id}/consulto` (router del modulo).

Verifica: a flag spento la rotta nuova risponde 404 anche senza token (app
vera) e le rotte /progettista esistenti restano invariate; a flag acceso solo
progettisti e admin (403 per un cliente), 404 per il progettista non
assegnato, 502 senza dati se l'audit non si scrive, forma JSON a whitelist;
il consulto dalla call risponde 201 con `partner_call_id`.

Dietro, il servizio vero sul primario finto di test_consulting_call_service."""

import httpx
import pytest
from fastapi import FastAPI

from app.api import deps
from app.api.routers import partner_calls, progettista
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.services import consulting_service
from app.services.partenariato_accesso import CallVistaProgettistaOut
from tests.fixtures.partenariati import esempio_guida as g
from tests.test_consulting_call_service import (  # noqa: F401 — fixture autouse
    ALTRO_PROG_USER,
    PROG_USER,
    ambiente_wp6,
    attiva,
    catalogo_e_fondo,
    consulto_assegnato,
    errore,
    fixture_fondo,
    scenario,
    utente,
)

RICHIESTA_FINTA = "e0000000-0000-4000-8000-00000000f00d"


def _http(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def flag(monkeypatch):
    def imposta(valore: bool) -> None:
        monkeypatch.setenv("PARTENARIATI_ATTIVO", "true" if valore else "false")
        get_settings.cache_clear()

    yield imposta
    get_settings.cache_clear()


def mini_app(db, sec, *, utente_corrente, active=None) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(progettista.router, prefix="/api/v1")
    app.include_router(partner_calls.router, prefix="/api/v1")
    app.dependency_overrides[deps.get_current_user] = lambda: utente_corrente
    app.dependency_overrides[deps.get_primary] = lambda: db
    app.dependency_overrides[deps.get_secondary] = lambda: sec
    if active is not None:
        app.dependency_overrides[deps.active_company] = lambda: active
    return app


class TestFlagSpento:
    async def test_404_senza_token_sull_app_vera(self, flag):
        flag(False)
        from app.main import app

        async with _http(app) as client:
            nuova = await client.get(f"/api/v1/progettista/richieste/{RICHIESTA_FINTA}/call")
            consulto = await client.post(
                f"/api/v1/partenariati/call/{g.CALL_GUIDA_ID}/consulto", json={})
        for resp in (nuova, consulto):
            assert resp.status_code == 404
            assert resp.json() == {"error": {"code": "not_found",
                                             "message": "Risorsa non trovata"}}

    async def test_le_rotte_progettista_esistenti_non_cambiano(self, flag, monkeypatch):
        """Il flag sta sulla SINGOLA rotta nuova: a flag spento le altre rotte
        del router rispondono come prima."""
        flag(False)
        chiamate: list = []

        async def list_pool(primary, user):
            chiamate.append(user["id"])
            return {"aperte": [], "assegnate": []}

        monkeypatch.setattr(consulting_service, "list_pool", list_pool)
        app = mini_app(object(), object(), utente_corrente=PROG_USER)
        async with _http(app) as client:
            pool = await client.get("/api/v1/progettista/richieste")
            nuova = await client.get(f"/api/v1/progettista/richieste/{RICHIESTA_FINTA}/call")
        assert pool.status_code == 200 and pool.json() == {"aperte": [], "assegnate": []}
        assert chiamate == [PROG_USER["id"]]
        assert nuova.status_code == 404

    async def test_rotta_registrata_nell_app(self, flag):
        flag(True)
        from app.main import app

        percorsi = app.openapi()["paths"]
        assert set(percorsi["/api/v1/progettista/richieste/{request_id}/call"]) == {"get"}
        assert set(percorsi["/api/v1/partenariati/call/{call_id}/consulto"]) == {"post"}


class TestFlagAcceso:
    async def test_progettista_assegnato(self, flag, fondo):
        flag(True)
        db, sec = await scenario()
        richiesta = await consulto_assegnato(db, sec, fondo)
        async with _http(mini_app(db, sec, utente_corrente=PROG_USER)) as client:
            resp = await client.get(f"/api/v1/progettista/richieste/{richiesta['id']}/call")
        assert resp.status_code == 200
        corpo = resp.json()
        assert set(corpo) == set(CallVistaProgettistaOut.model_fields)
        assert corpo["richiesta_id"] == richiesta["id"] and corpo["id"] == g.CALL_GUIDA_ID
        for interno in ("company_profile_id", "family_parent_id", "limiti", g.COMPANY["Y"],
                        g.OWNER["Y"]):
            assert interno not in resp.text

    async def test_admin_assegnato_come_progettista(self, flag, fondo):
        """Parità admin ↔ progettista (0019)."""
        flag(True)
        db, sec = await scenario()
        admin = {"id": "d0000000-0000-4000-8000-0000000000ad", "role": "admin",
                 "is_active": True}
        richiesta = await consulto_assegnato(db, sec, fondo)
        richiesta["assigned_progettista_id"] = admin["id"]
        async with _http(mini_app(db, sec, utente_corrente=admin)) as client:
            resp = await client.get(f"/api/v1/progettista/richieste/{richiesta['id']}/call")
        assert resp.status_code == 200

    async def test_cliente_403_non_assegnato_404(self, flag, fondo):
        flag(True)
        db, sec = await scenario()
        richiesta = await consulto_assegnato(db, sec, fondo)
        percorso = f"/api/v1/progettista/richieste/{richiesta['id']}/call"
        async with _http(mini_app(db, sec, utente_corrente=utente("X"))) as client:
            cliente = await client.get(percorso)
        async with _http(mini_app(db, sec, utente_corrente=ALTRO_PROG_USER)) as client:
            altro = await client.get(percorso)
            malformato = await client.get("/api/v1/progettista/richieste/non-un-id/call")
        assert (cliente.status_code, cliente.json()["error"]["code"]) == (403, "forbidden")
        assert (altro.status_code, altro.json()["error"]["code"]) == (404, "not_found")
        assert malformato.status_code == 422

    async def test_audit_non_scritto_502_senza_dati(self, flag, fondo):
        flag(True)
        db, sec = await scenario()
        richiesta = await consulto_assegnato(db, sec, fondo)
        db.guasti[("audit_log", "insert")] = errore("audit_giu")
        async with _http(mini_app(db, sec, utente_corrente=PROG_USER)) as client:
            resp = await client.get(f"/api/v1/progettista/richieste/{richiesta['id']}/call")
        assert resp.status_code == 502
        assert resp.json()["error"]["code"] == "upstream_error"
        assert g.CALL_GUIDA_ID not in resp.text

    async def test_consulto_dalla_call_201(self, flag):
        flag(True)
        db, sec = await scenario()
        app = mini_app(db, sec, utente_corrente=utente("X"), active=attiva("X"))
        async with _http(app) as client:
            resp = await client.post(f"/api/v1/partenariati/call/{g.CALL_GUIDA_ID}/consulto")
            doppio = await client.post(f"/api/v1/partenariati/call/{g.CALL_GUIDA_ID}/consulto")
        assert resp.status_code == 201
        assert resp.json()["partner_call_id"] == g.CALL_GUIDA_ID
        assert resp.json()["stato"] == "nuova"
        assert (doppio.status_code, doppio.json()["error"]["code"]) == (409, "conflict")

    async def test_consulto_dal_membro_403_e_da_altri_404(self, flag):
        flag(True)
        db, sec = await scenario()
        membro = mini_app(db, sec, utente_corrente=utente("X"),
                          active=attiva("X", editable=False))
        async with _http(membro) as client:
            resp = await client.post(f"/api/v1/partenariati/call/{g.CALL_GUIDA_ID}/consulto")
        assert (resp.status_code, resp.json()["error"]["code"]) == (403, "forbidden")
        altra = mini_app(db, sec, utente_corrente=utente("Y"), active=attiva("Y"))
        async with _http(altra) as client:
            resp = await client.post(f"/api/v1/partenariati/call/{g.CALL_GUIDA_ID}/consulto")
        assert resp.status_code == 404
        assert db.chiamate("fn_create_consultation_request") == []
