"""Flag del modulo partenariati (docs/partenariati.md, T1).

- `require_partenariati_attivo`: a flag spento 404 `{error: {code: not_found}}`
  anche SENZA token (mini-app con un router nuovo e con una rotta aggiunta a un
  router esistente, le cui altre rotte non cambiano);
- `MeOut.funzioni.partenariati` in `GET /me` segue il flag;
- i default delle Settings del modulo sono quelli di produzione."""

import os

import httpx
import pytest
from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel

from app.api import deps
from app.api.deps import CurrentUser, require_partenariati_attivo
from app.core.config import Settings, get_settings
from app.core.errors import NotFoundError, register_exception_handlers
from app.schemas.user import FunzioniOut, MeOut
from app.services import company_service, family_service, user_service

_OBBLIGATORIE = {
    "PRIMARY_SUPABASE_URL": "https://dummy.supabase.co",
    "PRIMARY_SUPABASE_SERVICE_ROLE_KEY": "k",
    "SECONDARY_SUPABASE_URL": "https://d2.supabase.co",
    "SECONDARY_SUPABASE_ANON_KEY": "k",
}


@pytest.fixture
def flag(monkeypatch):
    """Imposta il flag via ambiente (vince sul .env) e azzera la cache."""

    def imposta(valore: bool) -> None:
        for chiave, v in _OBBLIGATORIE.items():
            monkeypatch.setenv(chiave, v)
        monkeypatch.setenv("PARTENARIATI_ATTIVO", "true" if valore else "false")
        # indipendente dal .env locale, che Settings legge comunque
        monkeypatch.setenv("BILANCI_STORICO_ATTIVO", "false")
        get_settings.cache_clear()

    yield imposta
    get_settings.cache_clear()


# --- Settings --------------------------------------------------------------------


def test_default_di_produzione(monkeypatch):
    # le variabili della macchina non devono falsare il verdetto
    for chiave in list(os.environ):
        if chiave.upper().startswith("PARTENARIAT"):
            monkeypatch.delenv(chiave)
    s = Settings(
        _env_file=None,
        primary_supabase_url="https://dummy.supabase.co",
        primary_supabase_service_role_key="k",
        secondary_supabase_url="https://d2.supabase.co",
        secondary_supabase_anon_key="k",
    )
    attesi = {
        "partenariati_attivo": False,
        "partenariati_scheduler_attivo": True,
        "partenariati_ora_esecuzione": "05:30",
        "partenariato_ai_model": "claude-sonnet-5",
        "partenariato_ai_timeout_seconds": 180.0,
        "partenariato_ai_max_tokens": 16000,
        "partenariato_max_documenti": 4,
        "partenariato_pdf_max_bytes": 15_000_000,
        "partenariato_pdf_max_pagine": 150,
        "partenariato_pdf_timeout_seconds": 30.0,
        "partenariato_download_timeout_seconds": 20.0,
        "partenariato_max_caratteri_documenti": 180_000,
        "partenariato_limite_utente_giorno": 10,
        "partenariato_limite_utente_gratuito_giorno": 3,
        "partenariato_cooldown_bando_ore": 24,
        "partenariato_riverifica_giorni": 14,
        "partenariato_budget_cents_giorno": 500,
        "partenariati_ai_budget_cents_giorno_altri": 200,
        "partenariato_batch_budget_cents_giorno": 0,
        "partenariato_claim_ttl_seconds": 900,
    }
    assert {k: getattr(s, k) for k in attesi} == attesi


def test_il_flag_si_accende_da_ambiente(flag):
    flag(True)
    assert get_settings().partenariati_attivo is True
    flag(False)
    assert get_settings().partenariati_attivo is False


# --- Dipendenza --------------------------------------------------------------------


class Corpo(BaseModel):
    forza: bool


def _app_router_nuovo() -> FastAPI:
    """Router NUOVO del modulo: flag a livello di router."""
    router = APIRouter(prefix="/partenariati", dependencies=[Depends(require_partenariati_attivo)])

    @router.get("/risorsa")
    async def leggi(user: CurrentUser):
        return {"utente": user["id"]}

    @router.post("/risorsa")
    async def scrivi(user: CurrentUser, corpo: Corpo):
        return {"forza": corpo.forza}

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix="/api/v1")
    # Il primario non deve mai servire con il flag spento: un oggetto inerte
    # farebbe esplodere qualunque accesso.
    app.dependency_overrides[deps.get_primary] = lambda: object()
    return app


def _app_router_esistente() -> FastAPI:
    """Rotta aggiunta a un router ESISTENTE: flag sulla singola rotta."""
    router = APIRouter(prefix="/bandi")

    @router.get("")
    async def elenco():
        return {"ok": True}

    @router.get("/{slug}/partenariato", dependencies=[Depends(require_partenariati_attivo)])
    async def partenariato(slug: str):
        return {"slug": slug}

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix="/api/v1")
    return app


def _http(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


class TestRouterNuovo:
    async def test_flag_spento_404_senza_token(self, flag):
        flag(False)
        async with _http(_app_router_nuovo()) as client:
            resp = await client.get("/api/v1/partenariati/risorsa")
        assert resp.status_code == 404
        assert resp.json() == {"error": {"code": "not_found", "message": "Risorsa non trovata"}}

    async def test_flag_spento_404_anche_con_token_qualsiasi(self, flag):
        flag(False)
        async with _http(_app_router_nuovo()) as client:
            resp = await client.get(
                "/api/v1/partenariati/risorsa", headers={"Authorization": "Bearer non.un.jwt"}
            )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"

    async def test_flag_spento_404_prima_della_validazione_del_corpo(self, flag):
        flag(False)
        async with _http(_app_router_nuovo()) as client:
            resp = await client.post("/api/v1/partenariati/risorsa", json={"forza": "boh"})
        assert resp.status_code == 404

    async def test_flag_acceso_passa_all_autenticazione(self, flag):
        """Prova dell'ordine: col flag acceso la stessa richiesta senza token
        arriva a get_current_user (401), quindi a flag spento il 404 veniva
        davvero prima dell'autenticazione."""
        flag(True)
        async with _http(_app_router_nuovo()) as client:
            resp = await client.get("/api/v1/partenariati/risorsa")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "unauthorized"

    async def test_flag_acceso_utente_autenticato(self, flag):
        flag(True)
        app = _app_router_nuovo()
        app.dependency_overrides[deps.get_current_user] = lambda: {"id": "u1"}
        async with _http(app) as client:
            resp = await client.get("/api/v1/partenariati/risorsa")
        assert resp.status_code == 200
        assert resp.json() == {"utente": "u1"}


class TestRottaSuRouterEsistente:
    async def test_flag_spento_solo_la_rotta_nuova_in_404(self, flag):
        flag(False)
        async with _http(_app_router_esistente()) as client:
            vecchia = await client.get("/api/v1/bandi")
            nuova = await client.get("/api/v1/bandi/un-bando/partenariato")
        assert vecchia.status_code == 200
        assert nuova.status_code == 404
        assert nuova.json()["error"]["code"] == "not_found"

    async def test_flag_acceso_entrambe(self, flag):
        flag(True)
        async with _http(_app_router_esistente()) as client:
            nuova = await client.get("/api/v1/bandi/un-bando/partenariato")
        assert nuova.status_code == 200


async def test_dipendenza_diretta(flag):
    flag(False)
    with pytest.raises(NotFoundError) as exc:
        await require_partenariati_attivo()
    assert exc.value.message == "Risorsa non trovata"
    flag(True)
    assert await require_partenariati_attivo() is None


# --- GET /me: funzioni --------------------------------------------------------------


class _Risposta:
    def __init__(self, data):
        self.data = data


class _QueryProfilo:
    def __init__(self, riga):
        self._riga = riga

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    async def execute(self):
        return _Risposta([self._riga])


class _PrimarioFinto:
    def __init__(self, riga):
        self._riga = riga

    def table(self, nome):
        assert nome == "profiles"
        return _QueryProfilo(self._riga)


_PROFILO = {
    "id": "11111111-1111-1111-1111-111111111111",
    "email": "titolare@example.com",
    "nome": "Ada",
    "cognome": "Lovelace",
    "azienda": None,
    "telefono": None,
    "codice_fiscale": None,
    "cf_verified_at": None,
    "job_position_id": None,
    "job_positions": None,
    "job_position_altro": None,
    "role": "cliente",
    "is_active": True,
    "created_at": "2026-01-01T00:00:00+00:00",
    "user_subscriptions": [],
}


@pytest.fixture
def me_senza_famiglia(monkeypatch):
    async def family_limit(_primary, _uid):
        return 1

    async def get_membership(_primary, _uid):
        return None

    async def build_me_family(*_a, **_k):
        return None

    async def effective_max_aziende(_primary, _uid):
        return 1

    monkeypatch.setattr(family_service, "family_limit", family_limit)
    monkeypatch.setattr(family_service, "get_membership", get_membership)
    monkeypatch.setattr(family_service, "build_me_family", build_me_family)
    monkeypatch.setattr(company_service, "effective_max_aziende", effective_max_aziende)


class TestMeFunzioni:
    @pytest.mark.parametrize("attivo", [True, False])
    async def test_segue_il_flag(self, flag, me_senza_famiglia, attivo):
        flag(attivo)
        me = await user_service.get_me(_PrimarioFinto(_PROFILO), _PROFILO["id"])
        assert me.funzioni == FunzioniOut(partenariati=attivo)
        assert me.model_dump(mode="json")["funzioni"] == {
            "partenariati": attivo, "bilanci_storico": False
        }

    def test_default_spento(self):
        """Un MeOut costruito altrove (es. risposte admin) non accende nulla."""
        me = MeOut(profile=user_service.profile_from_row(_PROFILO))
        assert me.funzioni == FunzioniOut(partenariati=False)
