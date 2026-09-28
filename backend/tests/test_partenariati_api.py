"""API del WP3: flag del modulo (404 su ogni rotta nuova anche senza token,
sull'app vera), filtro `GET /bandi?partenariato=` (ignorato a flag spento,
lista vuota → pagina vuota senza interrogare il catalogo, id passati come
`id=in.(…)`), rotte del bando (202/200), vocabolario, admin solo admin."""

import httpx
import pytest
from fastapi import FastAPI
from postgrest import AsyncPostgrestClient

from app.api import deps
from app.api.deps import ActiveCompany
from app.api.routers import admin_partenariati, bandi, partenariati, partenariati_bandi
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.schemas.common import Page
from app.schemas.partenariato import PartenariatoBandoOut
from app.services import bandi_service
from app.services.bandi_service import BandiFilters, apply_filters, build_list_select

UTENTE = {"id": "a0000000-0000-0000-0000-000000000001", "role": "cliente", "is_active": True}
ADMIN = {**UTENTE, "id": "a0000000-0000-0000-0000-00000000000a", "role": "admin"}
ACTIVE = ActiveCompany(company_id="c0000000-0000-0000-0000-000000000001",
                       owner_id=UTENTE["id"], editable=True)


@pytest.fixture
def flag(monkeypatch):
    def imposta(valore: bool) -> None:
        for chiave, v in {
            "PRIMARY_SUPABASE_URL": "https://dummy.supabase.co",
            "PRIMARY_SUPABASE_SERVICE_ROLE_KEY": "k",
            "SECONDARY_SUPABASE_URL": "https://d2.supabase.co",
            "SECONDARY_SUPABASE_ANON_KEY": "k",
        }.items():
            monkeypatch.setenv(chiave, v)
        monkeypatch.setenv("PARTENARIATI_ATTIVO", "true" if valore else "false")
        get_settings.cache_clear()

    yield imposta
    get_settings.cache_clear()


class Inerte:
    """Client che non deve mai essere toccato."""

    def __getattr__(self, nome):
        raise AssertionError(f"accesso inatteso: {nome}")


def _http(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def mini_app(*routers, utente=UTENTE, primary=None, secondary=None, ai=None) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    for router in routers:
        app.include_router(router, prefix="/api/v1")
    if utente is not None:
        app.dependency_overrides[deps.get_current_user] = lambda: utente
    app.dependency_overrides[deps.active_company] = lambda: ACTIVE
    app.dependency_overrides[deps.get_primary] = lambda: primary or Inerte()
    app.dependency_overrides[deps.get_secondary] = lambda: secondary or Inerte()
    app.dependency_overrides[deps.get_ai] = lambda: ai or Inerte()
    return app


ROTTE_NUOVE = [
    ("GET", "/api/v1/bandi/un-bando/partenariato"),
    ("POST", "/api/v1/bandi/un-bando/partenariato/analisi"),
    ("GET", "/api/v1/partenariati/vocabolario"),
    ("GET", "/api/v1/admin/partenariati/estrazioni"),
    ("POST", "/api/v1/admin/partenariati/estrazioni/12"),
    ("POST", "/api/v1/admin/partenariati/run"),
]


# ------------------------------------------------------------ flag


class TestFlagSpento:
    @pytest.mark.parametrize(("metodo", "percorso"), ROTTE_NUOVE)
    async def test_404_senza_token_sull_app_vera(self, flag, metodo, percorso):
        flag(False)
        from app.main import app

        async with _http(app) as client:
            resp = await client.request(metodo, percorso, json={})
        assert resp.status_code == 404
        assert resp.json() == {"error": {"code": "not_found", "message": "Risorsa non trovata"}}

    @pytest.mark.parametrize(("metodo", "percorso"), ROTTE_NUOVE)
    async def test_404_anche_con_corpo_malformato(self, flag, metodo, percorso):
        """FastAPI legge il corpo PRIMA delle dipendenze: senza la route class
        del modulo un JSON rotto darebbe 422 e rivelerebbe la rotta."""
        flag(False)
        from app.main import app

        async with _http(app) as client:
            resp = await client.request(
                metodo, percorso, content=b"{", headers={"Content-Type": "application/json"}
            )
            inesistente = await client.request(
                metodo, "/api/v1/non-esiste", content=b"{",
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == inesistente.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"

    async def test_flag_acceso_corpo_malformato_resta_422(self, flag):
        flag(True)
        app = mini_app(partenariati_bandi.router)
        async with _http(app) as client:
            resp = await client.post(
                "/api/v1/bandi/un-bando/partenariato/analisi", content=b"{",
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 422

    async def test_ogni_rotta_dei_router_nuovi_e_coperta(self):
        dichiarate = {
            (metodo, "/api/v1" + rotta.path)
            for router in (partenariati_bandi.router, partenariati.router,
                           admin_partenariati.router)
            for rotta in router.routes
            for metodo in rotta.methods
        }
        normalizzate = {
            (m, p.replace("un-bando", "{slug}").replace("/12", "/{bando_id}"))
            for m, p in ROTTE_NUOVE
        }
        assert dichiarate == normalizzate

    async def test_con_token_qualsiasi_resta_404(self, flag):
        flag(False)
        from app.main import app

        async with _http(app) as client:
            resp = await client.get("/api/v1/partenariati/vocabolario",
                                    headers={"Authorization": "Bearer non-valido"})
        assert resp.status_code == 404


class TestFlagAcceso:
    async def test_senza_token_401(self, flag):
        flag(True)
        app = mini_app(partenariati.router, utente=None)
        async with _http(app) as client:
            resp = await client.get("/api/v1/partenariati/vocabolario")
        assert resp.status_code == 401

    async def test_vocabolario(self, flag):
        flag(True)
        async with _http(mini_app(partenariati.router)) as client:
            resp = await client.get("/api/v1/partenariati/vocabolario")
        assert resp.status_code == 200
        corpo = resp.json()
        assert corpo["versione"] == 1
        assert {"tipi_soggetto", "competenze", "forme", "ruoli"} <= set(corpo)
        pmi = next(t for t in corpo["tipi_soggetto"] if t["codice"] == "pmi")
        assert pmi["beneficiari"] == [27]


# ------------------------------------------------------------ rotte del bando


def dto(**campi) -> PartenariatoBandoOut:
    return PartenariatoBandoOut(bando_id=1, bando_slug="un-bando", stato="in_corso", **campi)


class TestRotteBando:
    async def test_get(self, flag, monkeypatch):
        flag(True)
        chiamate = []

        async def get_stato(primary, secondary, slug, *, ai=None):
            chiamate.append(slug)
            return dto(fase="lettura")

        monkeypatch.setattr("app.services.partenariato_service.get_stato", get_stato)
        async with _http(mini_app(partenariati_bandi.router)) as client:
            resp = await client.get("/api/v1/bandi/un-bando/partenariato")
        assert resp.status_code == 200 and chiamate == ["un-bando"]
        corpo = resp.json()
        assert corpo["stato"] == "in_corso" and corpo["fase"] == "lettura"
        assert corpo["calls_aperte"] == 0 and corpo["regole"] is None
        # nessun campo interno esposto
        assert "claim_token" not in corpo and "extraction" not in corpo

    @pytest.mark.parametrize(("avviata", "status"), [(True, 202), (False, 200)])
    async def test_post_202_se_avviata_200_altrimenti(self, flag, monkeypatch, avviata, status):
        flag(True)
        ricevuti = []

        async def avvia(primary, secondary, ai, user, active, slug, *, forza=False):
            ricevuti.append((user["id"], active.company_id, slug, forza))
            return dto(), avviata

        monkeypatch.setattr("app.services.partenariato_service.avvia_analisi", avvia)
        async with _http(mini_app(partenariati_bandi.router)) as client:
            resp = await client.post("/api/v1/bandi/un-bando/partenariato/analisi",
                                     json={"forza": True})
        assert resp.status_code == status
        assert ricevuti == [(UTENTE["id"], ACTIVE.company_id, "un-bando", True)]

    async def test_post_senza_corpo(self, flag, monkeypatch):
        flag(True)
        ricevuti = []

        async def avvia(primary, secondary, ai, user, active, slug, *, forza=False):
            ricevuti.append(forza)
            return dto(), True

        monkeypatch.setattr("app.services.partenariato_service.avvia_analisi", avvia)
        async with _http(mini_app(partenariati_bandi.router)) as client:
            resp = await client.post("/api/v1/bandi/un-bando/partenariato/analisi")
        assert resp.status_code == 202 and ricevuti == [False]

    async def test_errori_nel_formato_del_progetto(self, flag, monkeypatch):
        flag(True)
        from app.core.errors import AppError

        async def avvia(*a, **k):
            raise AppError(429, "ai_sospesa_oggi", "L'analisi automatica è sospesa per oggi")

        monkeypatch.setattr("app.services.partenariato_service.avvia_analisi", avvia)
        async with _http(mini_app(partenariati_bandi.router)) as client:
            resp = await client.post("/api/v1/bandi/un-bando/partenariato/analisi", json={})
        assert resp.status_code == 429
        assert resp.json()["error"]["code"] == "ai_sospesa_oggi"

    async def test_non_collide_con_il_dettaglio_del_bando(self, flag, monkeypatch):
        flag(True)
        visti = []

        async def dettaglio(secondary, slug, **kwargs):
            visti.append(slug)
            from app.schemas.bando import BandoDetail

            return BandoDetail(id=1, slug=slug)

        async def lookups(secondary):
            from types import SimpleNamespace

            return SimpleNamespace(regioni=[])

        async def facets(primary, active, lookups):
            return None

        monkeypatch.setattr("app.services.bandi_service.fetch_bando_by_slug", dettaglio)
        monkeypatch.setattr("app.api.routers.bandi.get_lookups", lookups)
        monkeypatch.setattr("app.api.routers.bandi.get_company_facets", facets)
        app = mini_app(bandi.router, partenariati_bandi.router)
        async with _http(app) as client:
            resp = await client.get("/api/v1/bandi/un-bando")
        assert resp.status_code == 200 and visti == ["un-bando"]


# ------------------------------------------------------------ filtro /bandi


class FakePrimaryIds:
    def __init__(self, ids):
        self.ids = ids
        self.chiamate = []

    def rpc(self, nome, params):
        self.chiamate.append((nome, params))
        ids = self.ids

        class _R:
            async def execute(self_inner):
                from types import SimpleNamespace

                return SimpleNamespace(data=ids)

        return _R()


@pytest.fixture
def elenco(monkeypatch):
    """Cattura i filtri passati a fetch_bandi (e se il catalogo viene letto)."""
    stato = {"filtri": None, "lookups": 0}

    async def fetch_bandi(secondary, filters, page, page_size, sort, **kwargs):
        stato["filtri"] = filters
        return Page.build([], 0, page, page_size)

    async def lookups(secondary):
        stato["lookups"] += 1
        from types import SimpleNamespace

        return SimpleNamespace(regioni=[])

    async def facets(primary, active, lookups):
        return None

    monkeypatch.setattr(bandi_service, "fetch_bandi", fetch_bandi)
    monkeypatch.setattr("app.api.routers.bandi.get_lookups", lookups)
    monkeypatch.setattr("app.api.routers.bandi.get_company_facets", facets)
    return stato


class TestFiltroBandi:
    async def test_ignorato_a_flag_spento(self, flag, elenco):
        flag(False)
        primary = FakePrimaryIds([1, 2])
        async with _http(mini_app(bandi.router, primary=primary)) as client:
            resp = await client.get("/api/v1/bandi", params={"partenariato": "ammesso"})
        assert resp.status_code == 200
        assert elenco["filtri"].bando_ids is None
        assert primary.chiamate == []

    @pytest.mark.parametrize("attivo", [True, False])
    @pytest.mark.parametrize("valore", ["forse", "x" * 300, ""])
    async def test_valore_ignoto_ignorato(self, flag, elenco, attivo, valore):
        flag(attivo)
        primary = FakePrimaryIds([1])
        async with _http(mini_app(bandi.router, primary=primary)) as client:
            resp = await client.get("/api/v1/bandi", params={"partenariato": valore})
        assert resp.status_code == 200
        assert elenco["filtri"].bando_ids is None and primary.chiamate == []

    async def test_lista_vuota_pagina_vuota_senza_catalogo(self, flag, elenco):
        flag(True)
        primary = FakePrimaryIds([])
        async with _http(mini_app(bandi.router, primary=primary)) as client:
            resp = await client.get("/api/v1/bandi",
                                    params={"partenariato": "obbligatorio", "page": 2})
        assert resp.status_code == 200
        assert resp.json() == {"items": [], "total": 0, "page": 2, "page_size": 20,
                               "total_pages": 0}
        assert elenco["filtri"] is None and elenco["lookups"] == 0
        assert primary.chiamate == [("fn_partenariato_bando_ids",
                                     {"p_modalita": ["obbligatorio"], "p_limite": 500})]

    async def test_id_passati_al_catalogo(self, flag, elenco):
        flag(True)
        primary = FakePrimaryIds([7, 5])
        async with _http(mini_app(bandi.router, primary=primary)) as client:
            resp = await client.get("/api/v1/bandi", params={"partenariato": "ammesso"})
        assert resp.status_code == 200
        assert elenco["filtri"].bando_ids == [7, 5]
        assert primary.chiamate[0][1]["p_modalita"] == ["ammesso", "obbligatorio"]

    def test_filtro_id_in_sulla_query(self):
        client = AsyncPostgrestClient("http://localhost:54321/rest/v1")
        filtri = BandiFilters(bando_ids=[5, 7])
        query = apply_filters(client.from_("bando").select(build_list_select(filtri)), filtri)
        valori = [v for k, v in query.request.params.multi_items() if k == "id"]
        assert valori == ["in.(5,7)"]
        senza = apply_filters(client.from_("bando").select("id"), BandiFilters())
        assert "id" not in dict(senza.request.params.multi_items())

    def test_url_della_lista_entro_il_limite_dei_gateway(self):
        """Con il tetto di id (a 7 cifre) e le faccette dell'azienda la riga di
        richiesta resta sotto gli 8 KB di default dei gateway basati su nginx,
        con margine per gli altri filtri (misurata con il builder reale)."""
        from datetime import date

        from app.services.partenariato_service import LIMITE_FILTRO

        client = AsyncPostgrestClient("https://abcdefghijklmnopqrst.supabase.co/rest/v1")
        filtri = BandiFilters(bando_ids=list(range(1_000_000, 1_000_000 + LIMITE_FILTRO)))
        oggi = date(2026, 9, 28)
        query = client.from_("bando").select(
            build_list_select(filtri, include_facets=True), count="exact"
        )
        query = bandi_service.apply_open_tier(apply_filters(query, filtri, oggi), oggi)
        query = query.order("data_pubblicazione", desc=True, nullsfirst=False).order("id")
        richiesta = query.range(0, 19).request
        http = richiesta.session.build_request(
            richiesta.http_method, str(richiesta.path), params=richiesta.params
        )
        assert len(http.url.raw_path) < 6500

    async def test_fetch_bandi_con_lista_vuota_non_interroga(self):
        pagina = await bandi_service.fetch_bandi(Inerte(), BandiFilters(bando_ids=[]), 1, 20,
                                                 "pubblicazione_desc")
        assert pagina.total == 0 and pagina.items == []


# ------------------------------------------------------------ admin


class FakePrimaryAdmin:
    def __init__(self):
        self.query = []

    def table(self, nome):
        primario = self

        class _Q:
            def select(self, *a, **k):
                return self

            def eq(self, c, v):
                primario.query.append((c, v))
                return self

            def order(self, *a, **k):
                return self

            def range(self, *a):
                return self

            async def execute(self):
                from types import SimpleNamespace

                return SimpleNamespace(data=[{
                    "bando_id": 1, "bando_slug": "un-bando", "bando_titolo": "Bando",
                    "stato": "pronta", "esito": "estratta", "fase": None,
                    "modalita_effettiva": "ammesso", "model": "claude-sonnet-5",
                    "cost_cents": 14, "input_tokens": 30000, "output_tokens": 6000,
                    "estratta_at": "2026-09-28T10:00:00+00:00", "verificata_at": None,
                    "ultima_esecuzione_at": None, "errore_codice": None, "tentativi_falliti": 0,
                    "prossimo_tentativo_at": None, "updated_at": "2026-09-28T10:00:00+00:00",
                }], count=1)

        return _Q()


class TestAdmin:
    @pytest.mark.parametrize(("metodo", "percorso"), [r for r in ROTTE_NUOVE if "admin" in r[1]])
    async def test_solo_admin(self, flag, metodo, percorso):
        flag(True)
        async with _http(mini_app(admin_partenariati.router)) as client:
            resp = await client.request(metodo, percorso, json={})
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden"

    async def test_elenco_estrazioni(self, flag):
        flag(True)
        primary = FakePrimaryAdmin()
        app = mini_app(admin_partenariati.router, utente=ADMIN, primary=primary)
        async with _http(app) as client:
            resp = await client.get("/api/v1/admin/partenariati/estrazioni",
                                    params={"stato": "pronta", "esito": "estratta"})
        assert resp.status_code == 200
        corpo = resp.json()
        assert corpo["total"] == 1 and corpo["items"][0]["cost_cents"] == 14
        assert ("stato", "pronta") in primary.query and ("esito", "estratta") in primary.query

    async def test_forza_estrazione(self, flag, monkeypatch):
        flag(True)
        ricevuti = []

        async def forza(primary, secondary, ai, user, bando_id, *, ignora_cooldown=False):
            ricevuti.append((user["id"], bando_id, ignora_cooldown))
            return dto(), True

        monkeypatch.setattr("app.services.partenariato_service.forza_admin", forza)
        app = mini_app(admin_partenariati.router, utente=ADMIN)
        async with _http(app) as client:
            resp = await client.post("/api/v1/admin/partenariati/estrazioni/12",
                                     json={"ignora_cooldown": True})
        assert resp.status_code == 202
        assert ricevuti == [(ADMIN["id"], 12, True)]

    async def test_run_409_se_gia_eseguita_e_ripeti(self, flag, monkeypatch):
        flag(True)
        eseguite = []

        async def claim(primary, giorno):
            return False

        async def esegui(primary, secondary, ai, oggi):
            eseguite.append(oggi)
            return {"failsafe_estrazioni": 0}

        monkeypatch.setattr("app.services.partenariati_scheduler.claim_run", claim)
        monkeypatch.setattr("app.services.partenariati_scheduler.esegui_run", esegui)
        app = mini_app(admin_partenariati.router, utente=ADMIN)
        async with _http(app) as client:
            resp = await client.post("/api/v1/admin/partenariati/run")
            assert resp.status_code == 409
            resp = await client.post("/api/v1/admin/partenariati/run", params={"ripeti": "true"})
        assert resp.status_code == 200
        assert resp.json()["riepilogo"] == {"failsafe_estrazioni": 0}
        assert len(eseguite) == 1
