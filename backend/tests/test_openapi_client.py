"""Test del client openapi.it: token manager, retry prudente (le chiamate
costano), envelope e flusso asincrono IT-full → IT-check_id.

Nessuna rete: il client accetta un http client iniettabile; le risposte reali
sono le fixture registrate dallo spike (tests/fixtures/openapi/).
"""

import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.clients.openapi import (
    OpenapiBilancioNonDisponibileError,
    OpenapiClient,
    OpenapiCreditoProviderError,
    OpenapiFormaNonAmmessaError,
    OpenapiIdentificativoNonValidoError,
    OpenapiInvalidIdError,
    OpenapiNessunDatoError,
    OpenapiNonInviataError,
    OpenapiRispostaTroppoGrandeError,
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

    def stream(self, method, url, **kwargs):
        """Come httpx.AsyncClient.stream: un context manager asincrono. Una
        FakeResponse in coda diventa un corpo JSON a pezzi."""
        self.requests.append((method.upper(), url))
        self.request_kwargs.append(kwargs)
        item = self._next()
        if isinstance(item, FakeResponse):
            item = json_stream(item.status_code, item._body)

        @asynccontextmanager
        async def aperto():
            yield item

        return aperto()

    async def aclose(self):
        pass


class FakeStream:
    """Risposta letta in streaming: pezzi di byte e header (Content-Length
    opzionale). `letti` conta i pezzi consumati."""

    def __init__(self, status_code: int, pezzi: list[bytes], headers: dict | None = None):
        self.status_code = status_code
        self.pezzi = pezzi
        self.headers = headers or {}
        self.letti = 0

    async def aiter_bytes(self):
        for pezzo in self.pezzi:
            self.letti += 1
            yield pezzo


def json_stream(status: int, body: dict, *, pezzo: int = 64, dichiara: bool = True) -> FakeStream:
    grezzo = json.dumps(body).encode()
    pezzi = [grezzo[i:i + pezzo] for i in range(0, len(grezzo), pezzo)] or [b""]
    headers = {"content-length": str(len(grezzo))} if dichiara else {}
    return FakeStream(status, pezzi, headers)


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

    def test_url_di_altri_servizi_path_intatto_senza_query(self):
        import logging

        from app.clients.openapi import _FILTRO_LOG_HTTPX

        record = logging.LogRecord(
            "httpx", logging.INFO, __file__, 1, 'HTTP Request: %s %s "%s %d %s"',
            ("GET", httpx.URL("https://dummy.supabase.co/rest/v1/x?id=eq.1"), "HTTP/1.1", 200,
             "OK"),
            None,
        )
        assert _FILTRO_LOG_HTTPX.filter(record) is True
        assert record.getMessage() == (
            'HTTP Request: GET https://dummy.supabase.co/rest/v1/x "HTTP/1.1 200 OK"'
        )

    def test_url_openapi_con_query_mascherato_e_senza_query(self):
        import logging

        from app.clients.openapi import _FILTRO_LOG_HTTPX

        record = logging.LogRecord(
            "httpx", logging.INFO, __file__, 1, 'HTTP Request: %s %s "%s %d %s"',
            ("GET", httpx.URL("https://company.openapi.com/IT-advanced/09876543217?x=1"),
             "HTTP/1.1", 200, "OK"),
            None,
        )
        assert _FILTRO_LOG_HTTPX.filter(record) is True
        messaggio = record.getMessage()
        assert "company.openapi.com/IT-advanced/*** " in messaggio
        assert "09876543217" not in messaggio and "?" not in messaggio


# ------------------------------------------------------------ bilancio ottico

PIVA_BO = "09876543217"
VISURE = "https://visurecamerali.openapi.it"


def envelope(data, *, success: bool = True, error=None, message: str = "") -> dict:
    return {"data": data, "success": success, "message": message, "error": error}


def richiesta_bo(**extra) -> dict:
    return {
        "cf_piva_id": PIVA_BO, "anno_chiusura": None, "tipo": "bilancio-ottico",
        "stato_richiesta": "In ricerca", "timestamp_creation": 1783363351,
        "timestamp_last_update": 1783363351, "allegati": [], "callback": False,
        "owner": "account@example.com", "id": "6a4bf7252ba8a578e60896f2", **extra,
    }


class TestBilancioOtticoRichiedi:
    """POST bilancio-ottico (A PAGAMENTO): gruppo `visure`, rifiuti sincroni
    classificati, mai un secondo invio dopo la partenza."""

    async def test_accettata_body_url_e_gruppo(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(200, envelope(richiesta_bo(anno_chiusura="2024"))))
        data = await client.bilancio_ottico_richiedi(PIVA_BO, 2024)
        assert data["id"] == "6a4bf7252ba8a578e60896f2"
        assert http.requests[-1] == ("POST", f"{VISURE}/bilancio-ottico")
        # anno come STRINGA (OAS), nessuna callback
        assert http.request_kwargs[-1]["json"] == {"cf_piva_id": PIVA_BO, "anno_chiusura": "2024"}
        assert http.minted_scopes == [client._scopes("visure")]

    async def test_ultimo_disponibile_anno_null(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(200, envelope(richiesta_bo())))
        await client.bilancio_ottico_richiedi(PIVA_BO, None)
        assert http.request_kwargs[-1]["json"] == {"cf_piva_id": PIVA_BO, "anno_chiusura": None}

    def test_sandbox_host_di_test(self):
        client, _ = make_client(openapi_env="sandbox")
        assert client._hosts["visure"] == "https://test.visurecamerali.openapi.it"

    @pytest.mark.parametrize(
        ("status", "codice", "eccezione"),
        [
            (404, 278, OpenapiBilancioNonDisponibileError),
            (400, 213, OpenapiFormaNonAmmessaError),
            (400, 275, OpenapiIdentificativoNonValidoError),
            (406, 222, OpenapiIdentificativoNonValidoError),
        ],
        ids=["278", "213", "275", "406_222"],
    )
    async def test_rifiuti_sincroni_classificati(self, status, codice, eccezione):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(status, envelope(None, success=False, error=codice, message="no")))
        with pytest.raises(eccezione):
            await client.bilancio_ottico_richiedi(PIVA_BO, None)

    @pytest.mark.parametrize(("status", "codice"), [(402, 611), (402, 610), (400, 611)])
    async def test_credito_provider_critical(self, status, codice, caplog):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(status, envelope(None, success=False, error=codice)))
        caplog.set_level(logging.CRITICAL, logger="bandofit.openapi")
        with pytest.raises(OpenapiCreditoProviderError):
            await client.bilancio_ottico_richiedi(PIVA_BO, None)
        assert any(r.levelno == logging.CRITICAL for r in caplog.records)
        assert not any(PIVA_BO in r.getMessage() for r in caplog.records)

    async def test_5xx_esito_ignoto_non_non_inviata(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(503, envelope(None, success=False, error=None)))
        with pytest.raises(OpenapiUpstreamError) as exc:
            await client.bilancio_ottico_richiedi(PIVA_BO, None)
        assert not isinstance(exc.value, OpenapiNonInviataError)

    async def test_successo_senza_id_esito_ignoto(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(200, envelope({"stato_richiesta": "In ricerca"})))
        with pytest.raises(OpenapiUpstreamError) as exc:
            await client.bilancio_ottico_richiedi(PIVA_BO, None)
        assert not isinstance(exc.value, OpenapiNonInviataError)

    async def test_timeout_mai_ritentato(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(httpx.ReadTimeout("lenta"))
        with pytest.raises(OpenapiTimeoutError):
            await client.bilancio_ottico_richiedi(PIVA_BO, None)
        assert [m for m, _ in http.requests] == ["POST", "POST"]  # mint + UNA sola POST

    async def test_mint_visure_fallito_non_inviata(self):
        client, http = make_client()
        http.push(FakeResponse(401, {"success": False, "message": "API non attiva", "error": 120}))
        with pytest.raises(OpenapiNonInviataError):
            await client.bilancio_ottico_richiedi(PIVA_BO, None)
        assert [u for _, u in http.requests] == ["https://oauth.openapi.it/token"]

    async def test_connessione_rifiutata_due_volte_non_inviata(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(httpx.ConnectError("no"))
        http.push(httpx.ConnectError("ancora no"))
        with pytest.raises(OpenapiNonInviataError):
            await client.bilancio_ottico_richiedi(PIVA_BO, None)


class TestBilancioOtticoLetture:
    async def test_stato(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(200, envelope(richiesta_bo(stato_richiesta="Dati disponibili"))))
        data = await client.bilancio_ottico_stato("6a4bf7252ba8a578e60896f2")
        assert data["stato_richiesta"] == "Dati disponibili"
        assert http.requests[-1] == (
            "GET", f"{VISURE}/bilancio-ottico/6a4bf7252ba8a578e60896f2"
        )
        assert http.minted_scopes == [client._scopes("visure")]

    async def test_stato_payload_inatteso(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(200, envelope(["x"])))
        with pytest.raises(OpenapiUpstreamError):
            await client.bilancio_ottico_stato("id")

    async def test_lista(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(200, envelope([richiesta_bo(), "rumore", richiesta_bo(id="b")])))
        voci = await client.bilancio_ottico_lista()
        assert [v["id"] for v in voci] == ["6a4bf7252ba8a578e60896f2", "b"]
        assert http.requests[-1] == ("GET", f"{VISURE}/bilancio-ottico")

    async def test_lista_vuota_404_270(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(404, envelope(None, success=False, error=270)))
        assert await client.bilancio_ottico_lista() == []

    @pytest.mark.parametrize(
        ("status", "body"),
        [
            (404, envelope(None, success=False, error=None)),  # 404 di routing/gateway
            (404, envelope(None, success=False, error=999)),  # 404 con un altro codice
            (400, envelope(None, success=False, error=270)),  # 270 con un altro status
            (200, envelope(None)),  # successo senza data
            (200, envelope({"richieste": [], "stato": "ok"})),  # oggetto senza id
        ],
        ids=["404_senza_codice", "404_altro_codice", "270_non_404", "data_null", "wrapper"],
    )
    async def test_lista_dubbia_fallisce_chiusa(self, status, body):
        # Una lista vuota vale come prova di non invio (e rimborso): ogni
        # forma dubbia deve sollevare, mai restituire [].
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(status, body))
        with pytest.raises(OpenapiUpstreamError):
            await client.bilancio_ottico_lista()

    async def test_lista_oggetto_singolo(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(200, envelope(richiesta_bo())))
        assert [v["id"] for v in await client.bilancio_ottico_lista()] == [
            "6a4bf7252ba8a578e60896f2"
        ]

    async def test_lista_paginata_parziale_con_le_voci(self):
        from app.clients.openapi import OpenapiListaParzialeError

        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(200, envelope([richiesta_bo()]) | {"next": "/bilancio-ottico?p=2"}))
        with pytest.raises(OpenapiListaParzialeError) as exc:
            await client.bilancio_ottico_lista()
        assert [v["id"] for v in exc.value.voci] == ["6a4bf7252ba8a578e60896f2"]

    async def test_lista_oltre_il_tetto(self, monkeypatch):
        import app.clients.openapi as modulo

        monkeypatch.setattr(modulo, "_MAX_LISTA_BILANCI_BYTES", 100)
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(200, envelope([richiesta_bo()] * 5)))
        with pytest.raises(OpenapiRispostaTroppoGrandeError):
            await client.bilancio_ottico_lista()

    async def test_impresa(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(200, envelope([{
            "id": "x", "denominazione": "ALFA SRL",
            "chiamate_disponibili": ["visurecamerali.openapi.it/bilancio-ottico"],
        }])))
        imprese = await client.impresa(PIVA_BO)
        assert imprese[0]["chiamate_disponibili"] == ["visurecamerali.openapi.it/bilancio-ottico"]
        assert http.requests[-1] == ("GET", f"{VISURE}/impresa/{PIVA_BO}")

    async def test_impresa_oggetto_e_404(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(200, envelope({"id": "x", "chiamate_disponibili": []})))
        assert await client.impresa(PIVA_BO) == [{"id": "x", "chiamate_disponibili": []}]
        http.push(FakeResponse(404, envelope(None, success=False, error=None)))
        assert await client.impresa(PIVA_BO) == []


class TestBilancioOtticoAllegati:
    """Allegati: ZIP in base64 dentro il JSON, letto in STREAMING con un
    tetto di byte controllato PRIMA del parse."""

    ALLEGATO = {"nome": "x.zip", "dimensione": 3, "file": "UEsF"}

    async def test_allegati_ok(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(200, envelope(self.ALLEGATO)))
        data = await client.bilancio_ottico_allegati("pid", max_bytes=10_000)
        assert data == self.ALLEGATO
        assert http.requests[-1] == ("GET", f"{VISURE}/bilancio-ottico/pid/allegati")

    async def test_non_ancora_disponibili_422_273(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(422, envelope(None, success=False, error=273)))
        assert await client.bilancio_ottico_allegati("pid", max_bytes=10_000) is None

    async def test_senza_file_errore(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(200, envelope({"nome": "x.zip", "dimensione": 0})))
        with pytest.raises(OpenapiUpstreamError):
            await client.bilancio_ottico_allegati("pid", max_bytes=10_000)

    async def test_content_length_oltre_il_tetto_senza_leggere(self):
        client, http = make_client()
        http.push(token_ok())
        flusso = json_stream(200, envelope({**self.ALLEGATO, "file": "A" * 5000}))
        http.push(flusso)
        with pytest.raises(OpenapiRispostaTroppoGrandeError) as exc:
            await client.bilancio_ottico_allegati("pid", max_bytes=1000)
        assert exc.value.motivo == "risposta_troppo_grande"
        assert flusso.letti == 0  # nessun byte letto: basta l'header

    async def test_corpo_oltre_il_tetto_senza_content_length(self):
        client, http = make_client()
        http.push(token_ok())
        flusso = json_stream(
            200, envelope({**self.ALLEGATO, "file": "A" * 5000}), pezzo=100, dichiara=False
        )
        http.push(flusso)
        with pytest.raises(OpenapiRispostaTroppoGrandeError):
            await client.bilancio_ottico_allegati("pid", max_bytes=1000)
        # lettura interrotta appena superato il tetto, non a fine corpo
        assert flusso.letti == 11 < len(flusso.pezzi)

    async def test_content_length_falso_non_inganna(self):
        client, http = make_client()
        http.push(token_ok())
        flusso = json_stream(200, envelope({**self.ALLEGATO, "file": "A" * 5000}), pezzo=500)
        flusso.headers["content-length"] = "10"
        http.push(flusso)
        with pytest.raises(OpenapiRispostaTroppoGrandeError):
            await client.bilancio_ottico_allegati("pid", max_bytes=1000)

    async def test_streaming_con_httpx_vero(self):
        import base64

        grande = base64.b64encode(b"Z" * 3000).decode()

        def risposta(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/token":
                return httpx.Response(
                    200, json=fixture("token_sample") | {"expire": int(time.time()) + 3600}
                )
            if request.url.path.endswith("/piccolo/allegati"):
                return httpx.Response(200, json=envelope(self.ALLEGATO))
            return httpx.Response(200, json=envelope({**self.ALLEGATO, "file": grande}))

        http = httpx.AsyncClient(transport=httpx.MockTransport(risposta))
        client = OpenapiClient(settings(), http=http)  # type: ignore[arg-type]
        try:
            assert (await client.bilancio_ottico_allegati("piccolo", max_bytes=2000)) == (
                self.ALLEGATO
            )
            with pytest.raises(OpenapiRispostaTroppoGrandeError):
                await client.bilancio_ottico_allegati("grande", max_bytes=2000)
        finally:
            await http.aclose()

    async def test_401_rigenera_il_token_anche_in_streaming(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(401, {"success": False, "message": "expired", "error": None}))
        http.push(token_ok())
        http.push(FakeResponse(200, envelope(self.ALLEGATO)))
        assert await client.bilancio_ottico_allegati("pid", max_bytes=10_000) == self.ALLEGATO
        assert http.minted_scopes == [client._scopes("visure"), client._scopes("visure")]


class TestEnvelopeMalformato:
    async def test_json_non_envelope_errore_upstream(self):
        client, http = make_client()
        http.push(token_ok())
        http.push(FakeResponse(200, ["non", "envelope"]))  # type: ignore[arg-type]
        with pytest.raises(OpenapiUpstreamError):
            await client.verifica_cf("RSSMRA80A01H501U")
