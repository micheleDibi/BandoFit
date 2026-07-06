"""Test del client openapi.it: token manager, retry prudente (le chiamate
costano), envelope e flusso asincrono IT-full → IT-check_id.

Nessuna rete: il client accetta un http client iniettabile; le risposte reali
sono le fixture registrate dallo spike (tests/fixtures/openapi/).
"""

import json
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.clients.openapi import (
    OpenapiClient,
    OpenapiInvalidIdError,
    OpenapiWrongTypeError,
)
from app.core.errors import (
    OpenapiNotConfiguredError,
    OpenapiTimeoutError,
    OpenapiUpstreamError,
)

FIXTURES = Path(__file__).parent / "fixtures" / "openapi"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def settings(**overrides) -> SimpleNamespace:
    base = dict(
        openapi_email="test@example.com",
        openapi_api_key="chiave",
        openapi_env="production",
        openapi_timeout_seconds=5.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class FakeResponse:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body

    def json(self) -> dict:
        return self._body


class FakeHTTP:
    """Coda di risposte/eccezioni; registra ogni richiesta effettuata."""

    def __init__(self):
        self.queue: list = []
        self.requests: list[tuple[str, str]] = []

    def push(self, item) -> None:
        self.queue.append(item)

    def _next(self):
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def post(self, url, **kwargs):
        self.requests.append(("POST", url))
        return self._next()

    async def get(self, url, **kwargs):
        self.requests.append(("GET", url))
        return self._next()

    async def request(self, method, url, **kwargs):
        self.requests.append((method.upper(), url))
        return self._next()

    async def aclose(self):
        pass


def token_ok(expire_offset: int = 3600) -> FakeResponse:
    body = fixture("token_sample") | {"expire": int(time.time()) + expire_offset}
    return FakeResponse(200, body)


def make_client(**overrides) -> tuple[OpenapiClient, FakeHTTP]:
    http = FakeHTTP()
    client = OpenapiClient(settings(**overrides), http=http)  # type: ignore[arg-type]
    return client, http


class TestConfigurazione:
    async def test_disattivato_senza_credenziali(self):
        client, _ = make_client(openapi_email="", openapi_api_key="")
        assert not client.enabled
        with pytest.raises(OpenapiNotConfiguredError):
            await client.it_full("01234567890")

    def test_sandbox_usa_host_di_test(self):
        client, _ = make_client(openapi_env="sandbox")
        assert client.sandbox
        assert all(scope.split(":")[1].startswith("test.") for scope in client._scopes())

    def test_produzione_usa_host_reali(self):
        client, _ = make_client()
        assert not client.sandbox
        assert "GET:company.openapi.com/IT-full" in client._scopes()
        assert "GET:company.openapi.com/IT-check_id" in client._scopes()
        assert "GET:risk.openapi.com/IT-verifica_cf" in client._scopes()


class TestToken:
    async def test_mint_e_riuso(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(200, fixture("verifica_cf_sample")))
        http.push(FakeResponse(200, fixture("verifica_cf_sample")))
        await client.verifica_cf("RSSMRA80A01H501U")
        await client.verifica_cf("RSSMRA80A01H501U")
        # un solo POST /token per due chiamate prodotto
        assert [m for m, _ in http.requests].count("POST") == 1

    async def test_token_scaduto_rigenerato(self):
        client, http = make_client()
        http.push(token_ok(expire_offset=10))  # dentro il margine: subito "scaduto"
        http.push(FakeResponse(200, fixture("verifica_cf_sample")))
        http.push(token_ok())
        http.push(FakeResponse(200, fixture("verifica_cf_sample")))
        await client.verifica_cf("RSSMRA80A01H501U")
        await client.verifica_cf("RSSMRA80A01H501U")
        assert [m for m, _ in http.requests].count("POST") == 2

    async def test_mint_rifiutato(self):
        client, http = make_client()
        http.push(FakeResponse(401, {"success": False, "message": "Wrong Auth Data", "error": 120, "data": None}))
        with pytest.raises(OpenapiUpstreamError):
            await client.verifica_cf("RSSMRA80A01H501U")

    async def test_401_su_prodotto_rigenera_e_riprova_una_volta(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(401, {"success": False, "message": "expired", "error": None, "data": None}))
        http.push(token_ok())
        http.push(FakeResponse(200, fixture("verifica_cf_sample")))
        assert await client.verifica_cf("RSSMRA80A01H501U") is True
        assert [m for m, _ in http.requests].count("POST") == 2


class TestRetryPrudente:
    async def test_connect_error_ritentato_una_volta(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(httpx.ConnectError("rifiutata"))
        http.push(FakeResponse(200, fixture("verifica_cf_sample")))
        assert await client.verifica_cf("RSSMRA80A01H501U") is True

    async def test_connect_error_doppio_fallisce(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(httpx.ConnectError("rifiutata"))
        http.push(httpx.ConnectError("rifiutata"))
        with pytest.raises(OpenapiUpstreamError):
            await client.verifica_cf("RSSMRA80A01H501U")

    async def test_read_timeout_mai_ritentato(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(httpx.ReadTimeout("lenta"))
        with pytest.raises(OpenapiTimeoutError):
            await client.verifica_cf("RSSMRA80A01H501U")
        # POST token + UNA sola GET: nessun retry su esito ignoto
        assert len(http.requests) == 2


class TestEnvelope:
    async def test_id_non_valido(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(406, fixture("error_not_valid")))
        with pytest.raises(OpenapiInvalidIdError):
            await client.it_full("00000000000")

    async def test_success_false_generico(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(500, {"success": False, "message": "boom", "error": 999, "data": None}))
        with pytest.raises(OpenapiUpstreamError):
            await client.it_full("01234567890")


class TestItFull:
    async def test_risposta_sincrona(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(200, fixture("it_full_sample")))
        data = await client.it_full("14061981008")
        assert data["companyDetails"]["vatCode"] == "14061981008"

    async def test_flusso_asincrono_con_polling(self, monkeypatch):
        async def no_sleep(_):
            pass

        monkeypatch.setattr("app.clients.openapi.asyncio.sleep", no_sleep)
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(302, fixture("it_full_pending")))
        http.push(FakeResponse(302, fixture("it_full_pending")))
        http.push(FakeResponse(200, fixture("it_full_sample")))
        data = await client.it_full("14061981008")
        assert data["companyDetails"]["companyName"].startswith("ENTE RICERCA")
        # il polling usa l'endpoint gratuito IT-check_id con l'id della richiesta
        poll_urls = [u for m, u in http.requests if "IT-check_id" in u]
        assert len(poll_urls) == 2
        assert poll_urls[0].endswith(fixture("it_full_pending")["data"]["id"])

    async def test_polling_esaurito(self, monkeypatch):
        async def no_sleep(_):
            pass

        monkeypatch.setattr("app.clients.openapi.asyncio.sleep", no_sleep)
        monkeypatch.setattr("app.clients.openapi._POLL_MAX_ATTEMPTS", 3)
        client, http = make_client()
        http.push(token_ok())
        for _ in range(5):
            http.push(FakeResponse(302, fixture("it_full_pending")))
        with pytest.raises(OpenapiTimeoutError):
            await client.it_full("14061981008")

    async def test_deadline_complessiva(self, monkeypatch):
        # La durata totale deve restare sotto il TTL del lock di import:
        # oltre la deadline si interrompe anche se i tentativi non sono finiti.
        monkeypatch.setattr("app.clients.openapi._TOTAL_DEADLINE_SECONDS", -1.0)
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(302, fixture("it_full_pending")))
        with pytest.raises(OpenapiTimeoutError):
            await client.it_full("14061981008")
        # nessun poll effettuato: solo POST token + prima GET
        assert len(http.requests) == 2


class TestVisure:
    async def test_richiesta_accettata(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(200, fixture("visura_request_accepted")))
        data = await client.visura_request("ordinaria-impresa-individuale", "14061981008")
        assert data["id"] and data["stato_richiesta"] == "In erogazione"
        # POST sull'endpoint della variante
        assert ("POST" in [m for m, u in http.requests if "ordinaria-impresa-individuale" in u][0])

    async def test_tipo_sbagliato_gratuito(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(404, fixture("visura_wrong_type")))
        with pytest.raises(OpenapiWrongTypeError):
            await client.visura_request("ordinaria-societa-capitale", "14061981008")

    async def test_status_evasa(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(200, fixture("visura_ready")))
        data = await client.visura_status("ordinaria-impresa-individuale", "6a4bf7252ba8a578e60896f2")
        assert data["stato_richiesta"] == "Dati disponibili"
        assert data["allegati"]

    async def test_allegati_senza_file_errore(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(200, {"data": {"nome": "x.zip"}, "success": True, "message": "", "error": None}))
        with pytest.raises(OpenapiUpstreamError):
            await client.visura_allegati("ordinaria-impresa-individuale", "req1")

    def test_scope_visure_incluse(self):
        client, _ = make_client()
        scopes = client._scopes()
        assert "POST:visurecamerali.openapi.it/ordinaria-societa-capitale" in scopes
        assert "GET:visurecamerali.openapi.it/ordinaria-impresa-individuale" in scopes


class TestVerificaCf:
    async def test_validita_true_e_false(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(200, {"data": {"validita": True}, "success": True, "message": "", "error": None}))
        http.push(FakeResponse(200, {"data": {"validita": False}, "success": True, "message": "", "error": None}))
        assert await client.verifica_cf("RSSMRA80A01H501U") is True
        assert await client.verifica_cf("XXXXXX00X00X000X") is False

    async def test_payload_inatteso(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(200, {"data": {}, "success": True, "message": "", "error": None}))
        with pytest.raises(OpenapiUpstreamError):
            await client.verifica_cf("RSSMRA80A01H501U")
