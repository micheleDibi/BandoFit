"""I log non contengono query string: né le righe di httpx per le chiamate in
uscita (qualsiasi host), né il log di accesso di uvicorn per le richieste in
entrata, né l'errore di rete registrato da main.py. Metodo, host, path ed esito
restano leggibili. httpcore, hpack e h2 restano muti sotto WARNING."""

import logging

import httpx
import pytest

from app.clients.openapi import url_per_log

TOKEN = "SENTINELLA-TOKEN-QUERY"
UTENTE = "utente-sentinella"
PASSWORD = "password-sentinella"


@pytest.fixture(autouse=True)
def stub_settings(monkeypatch):
    for chiave, valore in {
        "PRIMARY_SUPABASE_URL": "https://dummy.supabase.co",
        "PRIMARY_SUPABASE_SERVICE_ROLE_KEY": "k",
        "SECONDARY_SUPABASE_URL": "https://d2.supabase.co",
        "SECONDARY_SUPABASE_ANON_KEY": "k",
    }.items():
        monkeypatch.setenv(chiave, valore)
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestUrlPerLog:
    @pytest.mark.parametrize(("url", "atteso"), [
        ("https://d2.supabase.co/rest/v1/bando_pubblico?select=*&token=eq.x",
         "https://d2.supabase.co/rest/v1/bando_pubblico"),
        ("https://u:p@host.example:8443/a/b?x=1#frammento", "https://host.example:8443/a/b"),
        ("http://127.0.0.1:8000/", "http://127.0.0.1:8000/"),
        ("/api/v1/auth/invite-info?token=abc", "/api/v1/auth/invite-info"),
        ("/api/v1/bandi", "/api/v1/bandi"),
    ])
    def test_toglie_query_frammento_e_credenziali(self, url, atteso):
        assert url_per_log(url) == atteso

    def test_accetta_un_url_di_httpx(self):
        url = httpx.URL(f"https://{UTENTE}:{PASSWORD}@dummy.supabase.co/rest/v1/x?t={TOKEN}")
        assert url_per_log(url) == "https://dummy.supabase.co/rest/v1/x"


class TestHttpx:
    async def test_richiesta_postgrest_con_token_in_query_non_finisce_nel_log(self, caplog):
        def risposta(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[])

        http = httpx.AsyncClient(transport=httpx.MockTransport(risposta))
        caplog.set_level(logging.INFO, logger="httpx")
        try:
            await http.get(
                f"https://{UTENTE}:{PASSWORD}@dummy.supabase.co/rest/v1/email_suppressions",
                params={"select": "*", "token": f"eq.{TOKEN}", "apikey": TOKEN},
            )
        finally:
            await http.aclose()

        messaggi = [r.getMessage() for r in caplog.records if r.name == "httpx"]
        assert messaggi, "httpx non ha scritto la riga della richiesta"
        for messaggio in messaggi:
            assert TOKEN not in messaggio
            assert UTENTE not in messaggio and PASSWORD not in messaggio
            assert "?" not in messaggio
        assert any(
            m.startswith("HTTP Request: GET https://dummy.supabase.co/rest/v1/email_suppressions ")
            and "200 OK" in m
            for m in messaggi
        )

    def test_argomenti_che_non_sono_url_restano_intatti(self):
        from app.clients.openapi import _FILTRO_LOG_HTTPX

        record = logging.LogRecord(
            "httpx", logging.DEBUG, __file__, 1, "%s %s %d", ("a", None, 3), None
        )
        assert _FILTRO_LOG_HTTPX.filter(record) is True
        assert record.args == ("a", None, 3)


class TestMain:
    @pytest.mark.parametrize("nome", ["httpcore", "hpack", "hpack.hpack", "hpack.table", "h2"])
    def test_librerie_http_sotto_warning_restano_mute(self, nome):
        import app.main  # noqa: F401 — l'import configura i logger

        assert logging.getLogger(nome).getEffectiveLevel() == logging.WARNING

    def test_log_di_accesso_senza_query(self):
        from app import main

        accessi = logging.getLogger("uvicorn.access")
        assert main._FILTRO_ACCESSI in accessi.filters
        record = accessi.makeRecord(
            "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
            ("203.0.113.7:51234", "GET", f"/api/v1/auth/invite-info?token={TOKEN}", "1.1", 200),
            None,
        )
        assert main._FILTRO_ACCESSI.filter(record) is True
        assert record.getMessage() == (
            '203.0.113.7:51234 - "GET /api/v1/auth/invite-info HTTP/1.1" 200'
        )

    def test_log_di_accesso_senza_query_resta_uguale(self):
        from app import main

        record = logging.LogRecord(
            "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
            ("203.0.113.7:51234", "GET", "/api/v1/bandi", "1.1", 200), None,
        )
        main._FILTRO_ACCESSI.filter(record)
        assert record.getMessage() == '203.0.113.7:51234 - "GET /api/v1/bandi HTTP/1.1" 200'

    async def test_errore_di_rete_senza_query_nel_log(self, caplog):
        from app import main

        richiesta = httpx.Request(
            "GET", f"https://{UTENTE}:{PASSWORD}@dummy.supabase.co/rest/v1/x?token={TOKEN}"
        )
        errore = httpx.HTTPStatusError(
            f"Client error '404 Not Found' for url '{richiesta.url}'",
            request=richiesta, response=httpx.Response(404, request=richiesta),
        )
        with caplog.at_level(logging.ERROR, logger="bandofit"):
            risposta = await main.httpx_error_handler(None, errore)
        assert risposta.status_code == 504
        assert TOKEN not in caplog.text and PASSWORD not in caplog.text
        assert "HTTPStatusError GET https://dummy.supabase.co/rest/v1/x: stato 404" in caplog.text

    async def test_errore_di_rete_senza_richiesta_non_solleva(self, caplog):
        from app import main

        with caplog.at_level(logging.ERROR, logger="bandofit"):
            risposta = await main.httpx_error_handler(None, httpx.ReadTimeout("timed out"))
        assert risposta.status_code == 504
        assert "ReadTimeout richiesta non nota: timed out" in caplog.text
