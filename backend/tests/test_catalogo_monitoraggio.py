"""Monitoraggio del catalogo (contratto DB bandi §14; pannello admin
«Catalogo»): nessuna chiamata senza chiave, solo POST con la chiave nel
corpo, mappatura degli errori dal codice nel corpo, busta tollerante (chiavi
e codici nuovi) ma solo nella versione 1, cache di 60 s di qualunque esito
con una chiamata alla volta, timeout, la chiave mai in log, risposte o repr;
rotta riservata agli admin, sempre 200."""

import asyncio
import copy
import json
import logging
import secrets
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from postgrest import AsyncPostgrestClient
from postgrest.exceptions import APIError

from app.api import deps
from app.api.routers import admin_catalogo
from app.core.errors import register_exception_handlers
from app.services import catalogo_monitoraggio as cm

BUSTA = {
    "versione": 1,
    "generato_at": "2026-10-02T08:21:04+00:00",
    "calcolato_at": "2026-10-02T08:05:11+00:00",
    "aggiornato_at": "2026-10-02T08:05:12+00:00",
    "minuti_dal_calcolo": 15,
    "in_ritardo": False,
    "orologio_disallineato": False,
    "riepilogo": {
        "stato": "attenzione",
        "segnali": [{"codice": "fermi_in_lavorazione", "livello": "avviso",
                     "testo": "Alcuni bandi sono fermi in lavorazione da più di 13 ore.",
                     "dal": "2026-10-02T06:05:10+00:00", "misura": 3}],
        "non_misurati": [],
        "produttore": {"ultimo_giro_at": "2026-10-02T04:09:40+00:00", "ore_dall_ultimo_giro": 4.2,
                       "giri_24h": 4, "riavvii_24h": 0, "servizio": "attivo"},
        "giri": [{"id": 812, "giro": "06", "avviato_at": "2026-10-02T04:00:00+00:00",
                  "concluso_at": "2026-10-02T04:09:40+00:00", "durata_min": 9.7, "esito": "ok",
                  "interrotto_per_tetto": False, "passi_non_ok": []}],
        "controlli": [{"avviato_at": "2026-10-02T07:00:00+00:00", "esito": "ok",
                       "classificazioni": 40, "classificazioni_fallite": 0,
                       "eventi_non_applicati": 0}],
        "lavorazioni": [{"nome": "giro", "da_min": 3, "ttl_min": 90, "stato": "regolare"}],
        "ingresso": {"fermi_in_ingresso": 0, "fermi_in_lavorazione": 3,
                     "ultimo_bando_nuovo_at": "2026-10-01T16:02:00+00:00"},
        "eventi": {"ammessi_non_applicati": 0, "proposte_7g": 12, "in_attesa_pubblicazione": 0},
        "da_verificare": None,
        "job_orario": {"ultimo_avvio_at": "2026-10-02T08:05:00+00:00", "ultimo_esito": "succeeded",
                       "ultimo_ok_at": "2026-10-02T08:05:01+00:00", "falliti_24h": 0},
    },
}


def errore(codice, **altro) -> APIError:
    return APIError({"message": altro.get("message", "non autorizzato"), "code": codice,
                     "hint": None, "details": altro.get("details")})


class FakeRpc:
    def __init__(self, fake, funzione, parametri, opzioni):
        self.fake = fake
        self.chiamata = (funzione, parametri, opzioni)

    async def execute(self):
        self.fake.chiamate.append(self.chiamata)
        if self.fake.ritardo:
            await asyncio.sleep(self.fake.ritardo)
        if isinstance(self.fake.esito, BaseException):
            raise self.fake.esito
        return SimpleNamespace(data=self.fake.esito)


class FakeCatalogo:
    def __init__(self, esito=None, ritardo: float = 0):
        self.esito = copy.deepcopy(BUSTA) if esito is None else esito
        self.ritardo = ritardo
        self.chiamate: list = []

    def rpc(self, funzione, parametri, **opzioni):
        return FakeRpc(self, funzione, parametri, opzioni)


@pytest.fixture(autouse=True)
def impostazioni(monkeypatch):
    for nome, valore in {
        "PRIMARY_SUPABASE_URL": "https://dummy.supabase.co",
        "PRIMARY_SUPABASE_SERVICE_ROLE_KEY": "k",
        "SECONDARY_SUPABASE_URL": "https://d2.supabase.co",
        "SECONDARY_SUPABASE_ANON_KEY": "k",
    }.items():
        monkeypatch.setenv(nome, valore)
    monkeypatch.delenv("MONITORAGGIO_CATALOGO_CHIAVE", raising=False)
    from app.core.config import Settings, get_settings

    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    cm.azzera_cache()
    yield
    cm.azzera_cache()
    get_settings.cache_clear()


@pytest.fixture
def chiave(monkeypatch) -> str:
    """Una chiave finta, generata a ogni test."""
    from app.core.config import get_settings

    valore = secrets.token_urlsafe(48)
    monkeypatch.setenv("MONITORAGGIO_CATALOGO_CHIAVE", valore)
    get_settings.cache_clear()
    return valore


@pytest.fixture
def orologio(monkeypatch):
    adesso = {"t": 1000.0}
    monkeypatch.setattr(cm, "_orologio", lambda: adesso["t"])
    return adesso


# ----------------------------------------------------------------- chiamata


class TestChiamata:
    async def test_senza_chiave_non_chiama(self):
        catalogo = FakeCatalogo()
        esito = await cm.leggi(catalogo)
        assert esito.stato_accesso == "non_configurato" and esito.busta is None
        assert esito.letto_at.tzinfo is not None
        assert catalogo.chiamate == []

    async def test_post_con_la_chiave_nel_corpo(self, chiave):
        catalogo = FakeCatalogo()
        await cm.leggi(catalogo)
        # nessuna opzione: rpc() senza get=True è un POST con i parametri nel corpo
        assert catalogo.chiamate == [("monitoraggio_catalogo", {"p_chiave": chiave}, {})]

    async def test_con_il_client_postgrest_vero(self, chiave):
        """La libreria fa davvero un POST, con la chiave solo nel corpo, e gli
        errori arrivano come li mappa il servizio."""
        richieste: list[httpx.Request] = []
        risposte = iter([
            httpx.Response(200, json=BUSTA),
            httpx.Response(401, json={"code": "42501", "message": "non autorizzato",
                                      "details": None, "hint": None}),
            httpx.Response(404, json={"code": "PGRST202", "message": "x", "details": "y",
                                      "hint": None}),
            httpx.Response(401, json={"code": "PGRST301", "message": "x", "details": None,
                                      "hint": None}),
            httpx.Response(401, json={"message": "Invalid API key"}),  # gateway, senza code
            httpx.Response(503, text="<html>servizio non disponibile</html>"),
        ])

        def rispondi(richiesta: httpx.Request) -> httpx.Response:
            richieste.append(richiesta)
            return next(risposte)

        base = "https://catalogo.test/rest/v1"
        http = httpx.AsyncClient(base_url=base, transport=httpx.MockTransport(rispondi))
        client = AsyncPostgrestClient(base, http_client=http)
        stati = []
        for _ in range(6):
            cm.azzera_cache()
            stati.append((await cm.leggi(client)).stato_accesso)
        await http.aclose()

        assert stati == ["ok", "chiave_non_valida", "non_disponibile", "accesso_db_non_valido",
                         "accesso_db_non_valido", "non_raggiungibile"]
        for richiesta in richieste:
            assert richiesta.method == "POST"
            assert richiesta.url.path == "/rest/v1/rpc/monitoraggio_catalogo"
            assert chiave not in str(richiesta.url)
            assert json.loads(richiesta.content) == {"p_chiave": chiave}


# ----------------------------------------------------------------- errori


class TestErrori:
    @pytest.mark.parametrize(("eccezione", "stato"), [
        (errore("42501"), "chiave_non_valida"),
        (errore("PGRST202"), "non_disponibile"),
        (errore("PGRST301"), "accesso_db_non_valido"),
        (errore("PGRST302"), "accesso_db_non_valido"),
        # senza il corpo di PostgREST la libreria mette lo status HTTP in `code`
        (errore(401, message="JSON could not be generated"), "accesso_db_non_valido"),
        (errore(500, message="JSON could not be generated"), "non_raggiungibile"),
        (errore(503, message="JSON could not be generated"), "non_raggiungibile"),
        (errore("57014"), "non_raggiungibile"),
        (errore(None), "non_raggiungibile"),
        (httpx.ConnectError("rete giù"), "non_raggiungibile"),
        (httpx.ReadTimeout("lento"), "non_raggiungibile"),
        (RuntimeError("imprevisto"), "non_raggiungibile"),
    ])
    async def test_mappatura(self, chiave, eccezione, stato):
        esito = await cm.leggi(FakeCatalogo(esito=eccezione))
        assert esito.stato_accesso == stato and esito.busta is None

    async def test_timeout(self, chiave, monkeypatch):
        monkeypatch.setattr(cm, "TIMEOUT_SECONDI", 0.01)
        esito = await cm.leggi(FakeCatalogo(ritardo=1))
        assert esito.stato_accesso == "non_raggiungibile"

    def test_timeout_di_dieci_secondi(self):
        assert cm.TIMEOUT_SECONDI == 10 and cm.CACHE_SECONDI == 60


# ----------------------------------------------------------------- busta


class TestBusta:
    async def test_busta_valida(self, chiave):
        esito = await cm.leggi(FakeCatalogo())
        assert esito.stato_accesso == "ok"
        riepilogo = esito.busta.riepilogo
        assert esito.busta.versione == 1 and esito.busta.in_ritardo is False
        assert riepilogo.stato == "attenzione"
        assert riepilogo.segnali[0].testo.startswith("Alcuni bandi")
        assert riepilogo.produttore.ore_dall_ultimo_giro == 4.2
        assert riepilogo.giri[0].giro == "06" and riepilogo.giri[0].id == 812
        assert riepilogo.da_verificare is None
        assert riepilogo.job_orario.ultimo_esito == "succeeded"

    async def test_chiavi_e_valori_nuovi(self, chiave):
        busta = copy.deepcopy(BUSTA)
        busta["chiave_nuova"] = {"x": 1}
        busta["riepilogo"]["sezione_nuova"] = [1, 2]
        busta["riepilogo"]["stato"] = "stato_nuovo"
        busta["riepilogo"]["segnali"].append({
            "codice": "segnale_nuovo:passo_x", "livello": "allarme", "testo": "Testo fisso.",
            "dal": None, "misura": None, "extra": True,
        })
        busta["riepilogo"]["produttore"]["servizio"] = "valore_nuovo"
        busta["riepilogo"]["da_verificare"] = {"aperto": {"senza_conferma": 2},
                                               "chiusure_applicate_7g": 1}
        esito = await cm.leggi(FakeCatalogo(esito=busta))
        assert esito.stato_accesso == "ok"
        dump = esito.model_dump(mode="json")
        assert "chiave_nuova" not in dump["busta"]
        assert "sezione_nuova" not in dump["busta"]["riepilogo"]
        assert dump["busta"]["riepilogo"]["stato"] == "stato_nuovo"
        nuovo = dump["busta"]["riepilogo"]["segnali"][1]
        assert nuovo["codice"] == "segnale_nuovo:passo_x" and "extra" not in nuovo
        assert dump["busta"]["riepilogo"]["da_verificare"] == busta["riepilogo"]["da_verificare"]

    async def test_senza_riepilogo(self, chiave):
        busta = {"versione": 1, "generato_at": "2026-10-02T08:21:04+00:00", "calcolato_at": None,
                 "aggiornato_at": None, "minuti_dal_calcolo": None, "in_ritardo": True,
                 "orologio_disallineato": None, "riepilogo": None}
        esito = await cm.leggi(FakeCatalogo(esito=busta))
        assert esito.stato_accesso == "ok"
        assert esito.busta.riepilogo is None and esito.busta.in_ritardo is True

    @pytest.mark.parametrize("dati", [
        [], "testo", 1, {"generato_at": "2026-10-02T08:21:04+00:00"},
        {**BUSTA, "versione": 2}, {**BUSTA, "versione": True}, {**BUSTA, "versione": "1"},
        {**BUSTA, "versione": 1.5},
        {**BUSTA, "in_ritardo": "forse"},
        {**BUSTA, "riepilogo": {**BUSTA["riepilogo"], "segnali": "nessuno"}},
        {**BUSTA, "riepilogo": {**BUSTA["riepilogo"], "giri": [{"durata_min": "lunga"}]}},
    ])
    async def test_formato_non_supportato(self, chiave, dati):
        esito = await cm.leggi(FakeCatalogo(esito=dati))
        assert esito.stato_accesso == "formato_non_supportato" and esito.busta is None


# ----------------------------------------------------------------- cache


class TestCache:
    async def test_sessanta_secondi_poi_rilegge(self, chiave, orologio):
        catalogo = FakeCatalogo()
        primo = await cm.leggi(catalogo)
        orologio["t"] += 59
        secondo = await cm.leggi(catalogo)
        assert len(catalogo.chiamate) == 1 and secondo.letto_at == primo.letto_at
        orologio["t"] += 2
        terzo = await cm.leggi(catalogo)
        assert len(catalogo.chiamate) == 2 and terzo.letto_at >= primo.letto_at

    async def test_anche_gli_errori(self, chiave, orologio):
        catalogo = FakeCatalogo(esito=errore("42501"))
        for _ in range(3):
            assert (await cm.leggi(catalogo)).stato_accesso == "chiave_non_valida"
        assert len(catalogo.chiamate) == 1

    async def test_una_chiamata_alla_volta(self, chiave):
        catalogo = FakeCatalogo(ritardo=0.05)
        esiti = await asyncio.gather(*(cm.leggi(catalogo) for _ in range(5)))
        assert len(catalogo.chiamate) == 1
        assert {e.stato_accesso for e in esiti} == {"ok"}

    async def test_senza_chiave_mai_in_cache(self, orologio, monkeypatch, chiave):
        from app.core.config import get_settings

        monkeypatch.delenv("MONITORAGGIO_CATALOGO_CHIAVE")
        get_settings.cache_clear()
        assert (await cm.leggi(FakeCatalogo())).stato_accesso == "non_configurato"
        monkeypatch.setenv("MONITORAGGIO_CATALOGO_CHIAVE", chiave)
        get_settings.cache_clear()
        assert (await cm.leggi(FakeCatalogo())).stato_accesso == "ok"


# ----------------------------------------------------------------- segretezza


class TestSegretezza:
    @pytest.mark.parametrize("esito", [
        None, "formato",
        "errore_con_chiave", httpx.ConnectError("rete"), RuntimeError("imprevisto"),
    ])
    async def test_chiave_mai_in_log_risposte_o_repr(self, chiave, caplog, esito):
        if esito == "formato":
            esito = {"versione": 2}
        elif esito == "errore_con_chiave":
            # Anche se un corpo d'errore la riportasse, nel log va solo il codice.
            esito = errore("42501", message=f"rifiutata {chiave}", details=chiave)
        with caplog.at_level(logging.DEBUG):
            risposta = await cm.leggi(FakeCatalogo(esito=esito))
        testi = [caplog.text, risposta.model_dump_json(), repr(risposta), str(risposta)]
        assert all(chiave not in testo for testo in testi)
        if esito is not None:
            [riga] = [r for r in caplog.records if r.name == "bandofit.catalogo_monitoraggio"]
            assert riga.levelno == logging.WARNING
            assert risposta.stato_accesso in riga.getMessage()
            assert "rifiutata" not in riga.getMessage()


# ----------------------------------------------------------------- rotta


def _app(utente: dict | None, catalogo) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(admin_catalogo.router, prefix="/api/v1")
    if utente is not None:
        app.dependency_overrides[deps.get_current_user] = lambda: utente
    app.dependency_overrides[deps.get_primary] = lambda: object()
    app.dependency_overrides[deps.get_secondary] = lambda: catalogo
    return TestClient(app)


URL = "/api/v1/admin/catalogo/monitoraggio"


class TestRotta:
    def test_admin_200(self, chiave):
        risposta = _app({"id": "a1", "role": "admin"}, FakeCatalogo()).get(URL)
        assert risposta.status_code == 200
        corpo = risposta.json()
        assert corpo["stato_accesso"] == "ok"
        assert corpo["busta"]["riepilogo"]["stato"] == "attenzione"
        assert "letto_at" in corpo
        assert chiave not in risposta.text

    def test_non_configurato_200(self):
        risposta = _app({"id": "a1", "role": "admin"}, FakeCatalogo()).get(URL)
        assert risposta.status_code == 200
        assert risposta.json()["stato_accesso"] == "non_configurato"
        assert risposta.json()["busta"] is None

    def test_errore_del_catalogo_resta_200(self, chiave):
        catalogo = FakeCatalogo(esito=errore(503, message="JSON could not be generated"))
        risposta = _app({"id": "a1", "role": "admin"}, catalogo).get(URL)
        assert risposta.status_code == 200
        assert risposta.json()["stato_accesso"] == "non_raggiungibile"

    @pytest.mark.parametrize("ruolo", ["cliente", "progettista"])
    def test_non_admin_403(self, chiave, ruolo):
        catalogo = FakeCatalogo()
        risposta = _app({"id": "u1", "role": ruolo}, catalogo).get(URL)
        assert risposta.status_code == 403
        assert catalogo.chiamate == []

    def test_senza_token_401(self, chiave):
        catalogo = FakeCatalogo()
        risposta = _app(None, catalogo).get(URL)
        assert risposta.status_code == 401
        assert catalogo.chiamate == []
