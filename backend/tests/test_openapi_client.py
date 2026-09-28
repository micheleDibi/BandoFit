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
    OpenapiNessunDatoError,
    OpenapiNonInviataError,
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
        self.request_kwargs: list[dict] = []
        self.minted_scopes: list[list[str]] = []  # scope di ogni POST /token

    def push(self, item) -> None:
        self.queue.append(item)

    def _next(self):
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def post(self, url, **kwargs):
        self.requests.append(("POST", url))
        self.minted_scopes.append(list((kwargs.get("json") or {}).get("scopes") or []))
        return self._next()

    async def get(self, url, **kwargs):
        self.requests.append(("GET", url))
        return self._next()

    async def request(self, method, url, **kwargs):
        self.requests.append((method.upper(), url))
        self.request_kwargs.append(kwargs)
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
        # Aggiornato di proposito in WP1: la visura camerale resta rimossa e il
        # gruppo `core` (quello di IT-full, verifica CF, VIES) non ha scope
        # visurecamerali. Il bilancio-ottico (WP2) ha un token SEPARATO, gruppo
        # `visure` (vedi TestTokenPerGruppo): se non è attivato in console
        # fallisce solo il suo mint, mai quello di IT-full.
        assert not any("visurecamerali" in scope for scope in client._scopes())
        assert not any("visurecamerali" in scope for scope in client._scopes("core"))
        # L'invio fatture SDI è stato rimosso: nessuno scope verso sdi.openapi.
        assert not any("sdi.openapi" in scope for scope in client._scopes())


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


class TestVerificaPivaUe:
    """VIES (scope EU-start): prova per il reverse charge del venditore croato."""

    async def test_validita_true_e_false(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(200, {"data": {"valid": True}, "success": True, "message": "", "error": None}))
        http.push(FakeResponse(200, {"data": {"valid": False}, "success": True, "message": "", "error": None}))
        assert await client.verifica_piva_ue("DE", "123456789") is True
        assert await client.verifica_piva_ue("DE", "999999999") is False

    async def test_identificativo_rifiutato_e_non_valido(self):
        # HTTP 406 / error 222 = P.IVA malformata per il provider → False,
        # non un errore (il salvataggio prosegue al 25%).
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(406, {"success": False, "message": "not valid", "error": 222}))
        assert await client.verifica_piva_ue("DE", "XX") is False

    async def test_grecia_usa_il_prefisso_el(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(200, {"data": {"valid": True}, "success": True, "message": "", "error": None}))
        await client.verifica_piva_ue("GR", "123456789")
        assert http.requests[-1][1].endswith("/EU-start/EL123456789")

    async def test_timeout_dedicato_inoltrato(self):
        # Percorso interattivo: 8s, non i 30s del client (il down del VIES
        # non deve tenere appeso il form dell'anagrafica).
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(200, {"data": {"valid": True}, "success": True, "message": "", "error": None}))
        await client.verifica_piva_ue("FR", "12345678901")
        assert http.request_kwargs[-1].get("timeout") == 8.0


# ================================================================ WP1

def advanced_ok(dato: dict | None = None) -> FakeResponse:
    body = fixture("it_advanced_sintetico")
    if dato is not None:
        body = {**body, "data": [dato]}
    return FakeResponse(200, body)


class TestTokenPerGruppo:
    """Token separati per gruppo di scope: un prodotto non attivato in console
    non deve mai rompere IT-full."""

    def test_scope_core_invariati(self):
        client, _ = make_client()
        assert client._scopes() == client._scopes("core") == [
            "GET:company.openapi.com/IT-full",
            "GET:company.openapi.com/IT-check_id",
            "GET:company.openapi.com/EU-start",
            "GET:risk.openapi.com/IT-verifica_cf",
        ]

    def test_gruppi_advanced_e_visure_separati(self):
        client, _ = make_client()
        assert client._scopes("advanced") == ["GET:company.openapi.com/IT-advanced"]
        visure = client._scopes("visure")
        assert visure == [
            "POST:visurecamerali.openapi.it/bilancio-ottico",
            "GET:visurecamerali.openapi.it/bilancio-ottico",
            "GET:visurecamerali.openapi.it/impresa",
        ]
        # nessuna visura ordinaria/storica: solo bilancio e impresa
        assert not any("ordinaria" in s or "storica" in s for s in visure)
        # i gruppi non si sovrappongono
        assert not set(client._scopes("core")) & set(client._scopes("advanced"))
        assert not set(client._scopes("core")) & set(visure)

    def test_sandbox_host_di_test_per_tutti_i_gruppi(self):
        client, _ = make_client(openapi_env="sandbox")
        for gruppo in ("core", "advanced", "visure"):
            assert all(s.split(":")[1].startswith("test.") for s in client._scopes(gruppo))

    async def test_mint_visure_fallito_non_rompe_it_full(self):
        client, http = make_client()
        http.push(FakeResponse(401, {"success": False, "message": "API non attiva", "error": 120}))
        with pytest.raises(OpenapiNonInviataError):
            await client._get_token("visure")
        http.push(token_ok())
        http.push(FakeResponse(200, fixture("it_full_sample")))
        data = await client.it_full("14061981008")
        assert data["companyDetails"]["vatCode"] == "14061981008"
        # due mint distinti: visure (fallito) e core (con i soli scope core)
        assert http.minted_scopes[0] == client._scopes("visure")
        assert http.minted_scopes[1] == client._scopes("core")

    async def test_mint_fallito_per_rete_e_non_inviata(self):
        client, http = make_client()
        http.push(httpx.ConnectError("rifiutata"))
        with pytest.raises(OpenapiNonInviataError) as exc:
            await client.it_advanced("09876543217", timeout_s=25)
        # resta un OpenapiUpstreamError (502) per i chiamanti che non distinguono
        assert isinstance(exc.value, OpenapiUpstreamError)
        assert [m for m, _ in http.requests] == ["POST"]  # il prodotto non è partito

    async def test_401_rigenera_solo_il_token_del_gruppo(self):
        client, http = make_client()
        # core: mint + verifica
        http.push(token_ok())
        http.push(FakeResponse(200, fixture("verifica_cf_sample")))
        await client.verifica_cf("RSSMRA80A01H501U")
        # advanced: mint, 401, re-mint del SOLO gruppo advanced, ok
        http.push(token_ok())
        http.push(FakeResponse(401, {"success": False, "message": "expired", "error": None}))
        http.push(token_ok())
        http.push(advanced_ok())
        await client.it_advanced("09876543217", timeout_s=25)
        # core di nuovo: token ancora valido, nessun mint
        http.push(FakeResponse(200, fixture("verifica_cf_sample")))
        await client.verifica_cf("RSSMRA80A01H501U")
        assert http.minted_scopes == [
            client._scopes("core"),
            client._scopes("advanced"),
            client._scopes("advanced"),
        ]


class TestItAdvanced:
    async def test_array_restituisce_il_primo_elemento(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(advanced_ok())
        dato = await client.it_advanced("09876543217", timeout_s=25)
        assert dato["vatCode"] == "09876543217"
        assert len(dato["balanceSheets"]["all"]) == 8
        # URL, gruppo e tetto della singola chiamata
        assert http.requests[-1] == ("GET", "https://company.openapi.com/IT-advanced/09876543217")
        assert http.request_kwargs[-1]["timeout"] == 25
        assert http.minted_scopes == [["GET:company.openapi.com/IT-advanced"]]

    async def test_204_nessun_dato(self):
        class Vuota(FakeResponse):
            def json(self):  # come httpx su un corpo vuoto
                raise ValueError("Expecting value")

        client, http = make_client()
        http.push(token_ok())
        http.push(Vuota(204, {}))  # corpo vuoto: non va mai letto
        with pytest.raises(OpenapiNessunDatoError) as exc:
            await client.it_advanced("09876543217", timeout_s=25)
        assert exc.value.status == 204

    async def test_404_305_nessun_dato(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(404, {
            "success": False, "message": "no company matches this id", "error": 305, "data": None,
        }))
        with pytest.raises(OpenapiNessunDatoError) as exc:
            await client.it_advanced("09876543217", timeout_s=25)
        assert exc.value.status == 404

    async def test_array_vuoto_nessun_dato(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(200, {"success": True, "data": [], "message": "", "error": None}))
        with pytest.raises(OpenapiNessunDatoError):
            await client.it_advanced("09876543217", timeout_s=25)

    async def test_identificativo_rifiutato(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(406, fixture("error_not_valid")))
        with pytest.raises(OpenapiInvalidIdError):
            await client.it_advanced("00000000000", timeout_s=25)

    async def test_credito_esaurito_errore_upstream(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(402, {"success": False, "message": "credito", "error": 610}))
        with pytest.raises(OpenapiUpstreamError) as exc:
            await client.it_advanced("09876543217", timeout_s=25)
        assert not isinstance(exc.value, OpenapiNonInviataError)


class TestRitentoClassificato:
    """Dopo un ConnectError il ritento è classificato: solo ciò che non è
    partito è «non inviato» (rimborsabile); il resto è esito ignoto."""

    async def test_connect_poi_read_timeout_e_timeout_non_non_inviata(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(httpx.ConnectError("rifiutata"))
        http.push(httpx.ReadTimeout("lenta"))
        with pytest.raises(OpenapiTimeoutError):
            await client.it_advanced("09876543217", timeout_s=25)
        # il ritento è partito: nessun terzo tentativo
        assert [m for m, _ in http.requests] == ["POST", "GET", "GET"]

    @pytest.mark.parametrize("errore", [httpx.ConnectError("no"), httpx.PoolTimeout("pool")])
    async def test_connect_poi_non_partita_e_non_inviata(self, errore):
        client, http = make_client()
        http.push(token_ok())
        http.push(httpx.ConnectError("rifiutata"))
        http.push(errore)
        with pytest.raises(OpenapiNonInviataError):
            await client.it_advanced("09876543217", timeout_s=25)

    async def test_connect_poi_protocollo_rotto_e_esito_ignoto(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(httpx.ConnectError("rifiutata"))
        http.push(httpx.RemoteProtocolError("chiusa a metà"))
        with pytest.raises(OpenapiUpstreamError) as exc:
            await client.it_advanced("09876543217", timeout_s=25)
        assert not isinstance(exc.value, OpenapiNonInviataError)


class TestPreparaToken:
    async def test_mint_del_gruppo_una_volta(self):
        client, http = make_client()
        http.push(token_ok())
        await client.prepara_token("advanced")
        await client.prepara_token("advanced")  # già in cache: nessun secondo mint
        assert http.minted_scopes == [["GET:company.openapi.com/IT-advanced"]]

    async def test_mint_fallito_non_inviata(self):
        client, http = make_client()
        http.push(httpx.ConnectError("oauth giù"))
        with pytest.raises(OpenapiNonInviataError):
            await client.prepara_token("advanced")

    async def test_non_configurato(self):
        client, _ = make_client(openapi_email="", openapi_api_key="")
        with pytest.raises(OpenapiNotConfiguredError):
            await client.prepara_token("advanced")


class TestLogSenzaIdentificativi:
    """httpx logga a INFO ogni richiesta con l'URL completo e main.py porta il
    root logger a INFO: P.IVA e CF dei path non devono finire nei log (T8)."""

    async def test_url_mascherati_nel_log_di_httpx(self, caplog):
        import logging

        def risposta(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/token":
                return httpx.Response(
                    200, json=fixture("token_sample") | {"expire": int(time.time()) + 3600}
                )
            if "IT-verifica_cf" in request.url.path:
                return httpx.Response(200, json=fixture("verifica_cf_sample"))
            return httpx.Response(200, json=fixture("it_advanced_sintetico"))

        http = httpx.AsyncClient(transport=httpx.MockTransport(risposta))
        client = OpenapiClient(settings(), http=http)  # type: ignore[arg-type]
        caplog.set_level(logging.INFO, logger="httpx")
        try:
            await client.it_advanced("09876543217", timeout_s=25)
            await client.verifica_cf("RSSMRA85M01H501Q")
        finally:
            await http.aclose()

        messaggi = [r.getMessage() for r in caplog.records if r.name == "httpx"]
        assert any("company.openapi.com/IT-advanced/***" in m for m in messaggi)
        assert any("risk.openapi.com/IT-verifica_cf/***" in m for m in messaggi)
        assert not any("09876543217" in m or "RSSMRA85M01H501Q" in m for m in messaggi)
        # il resto della riga (metodo, esito) resta leggibile
        assert any(m.startswith("HTTP Request: GET") and "200 OK" in m for m in messaggi)

    def test_url_di_altri_servizi_intatti(self):
        import logging

        from app.clients.openapi import _FILTRO_LOG_HTTPX

        record = logging.LogRecord(
            "httpx", logging.INFO, __file__, 1, 'HTTP Request: %s %s "%s %d %s"',
            ("GET", httpx.URL("https://dummy.supabase.co/rest/v1/x?id=eq.1"), "HTTP/1.1", 200,
             "OK"),
            None,
        )
        assert _FILTRO_LOG_HTTPX.filter(record) is True
        assert "rest/v1/x?id=eq.1" in record.getMessage()
