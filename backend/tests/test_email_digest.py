"""Digest email dei nuovi bandi: header RFC 8058, escaping, card e formati."""

import logging

import httpx
import pytest

from app.services import email_service
from app.services.email_service import (
    _format_eur,
    send_bandi_digest_email,
    send_bandi_digest_email_multi,
)

UNSUB = "https://api.test.it/api/v1/alerts/unsubscribe?token=abc"
CTA = "https://app.test.it/app/bandi"


def bando(**overrides) -> dict:
    base = {
        "titolo": "Bando innovazione PMI",
        "ente_erogatore": "Regione Lombardia",
        "importo_eur": 1_500_000,
        "importo_max_eur": 50_000,
        "scadenza_label": "30/09/2026",
        "giorni_alla_scadenza": 40,
        "motivo": "Regioni: Lombardia · ATECO: 62",
        "url": "https://app.test.it/app/bandi/bando-innovazione",
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def stub_settings(monkeypatch):
    for key, value in {
        "PRIMARY_SUPABASE_URL": "https://dummy.supabase.co",
        "PRIMARY_SUPABASE_SERVICE_ROLE_KEY": "k",
        "SECONDARY_SUPABASE_URL": "https://d2.supabase.co",
        "SECONDARY_SUPABASE_ANON_KEY": "k",
    }.items():
        monkeypatch.setenv(key, value)
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestFormatEur:
    def test_migliaia_italiane(self):
        assert _format_eur(1_500_000) == "1.500.000 €"

    def test_none(self):
        assert _format_eur(None) is None


class TestDigestSmtp:
    @pytest.fixture
    def captured(self, monkeypatch):
        import aiosmtplib

        messages = []

        async def fake_send(message, **kwargs):
            messages.append(message)

        monkeypatch.setattr(aiosmtplib, "send", fake_send)
        monkeypatch.setenv("SMTP_HOST", "smtp.test.it")
        monkeypatch.setenv("EMAIL_FROM", "BandoFit <alerts@bandofit.it>")
        from app.core.config import get_settings

        get_settings.cache_clear()
        return messages

    async def test_header_rfc_8058(self, captured):
        assert await send_bandi_digest_email("dest@test.it", [bando()], CTA, UNSUB)
        [message] = captured
        assert message["List-Unsubscribe"] == f"<{UNSUB}>"
        assert message["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"

    async def test_multipart_e_contenuti(self, captured):
        await send_bandi_digest_email("dest@test.it", [bando(), bando(titolo="Altro")], CTA, UNSUB)
        [message] = captured
        assert message["Subject"] == "2 nuovi bandi per la tua azienda — BandoFit"
        html_part = message.get_body(("html",)).get_content()
        text_part = message.get_body(("plain",)).get_content()
        assert "Bando innovazione PMI" in html_part
        assert "1.500.000 €" in html_part
        assert "Regione Lombardia" in html_part
        assert "Perché lo vedi: Regioni: Lombardia" in html_part
        assert UNSUB in text_part
        assert "https://app.test.it/app/bandi/bando-innovazione" in text_part

    async def test_titolo_escapato(self, captured):
        await send_bandi_digest_email(
            "dest@test.it", [bando(titolo="Bando <b>furbo</b>")], CTA, UNSUB
        )
        html_part = captured[0].get_body(("html",)).get_content()
        assert "<b>furbo</b>" not in html_part
        assert "&lt;b&gt;furbo&lt;/b&gt;" in html_part

    async def test_badge_scadenza_al_confine(self, captured):
        await send_bandi_digest_email(
            "dest@test.it",
            [
                bando(giorni_alla_scadenza=14),
                bando(titolo="Lontano", giorni_alla_scadenza=15),
                bando(titolo="Oggi", giorni_alla_scadenza=0),
                bando(titolo="Aperto", scadenza_label=None, giorni_alla_scadenza=None),
            ],
            CTA,
            UNSUB,
        )
        html_part = captured[0].get_body(("html",)).get_content()
        assert "scade tra 14 giorni" in html_part
        assert "scade tra 15 giorni" not in html_part
        assert "scade oggi" in html_part
        assert "Senza scadenza dichiarata" in html_part

    async def test_oggetto_singolare(self, captured):
        await send_bandi_digest_email("dest@test.it", [bando()], CTA, UNSUB)
        assert captured[0]["Subject"] == "Un nuovo bando per la tua azienda — BandoFit"


class TestDigestMulti:
    @pytest.fixture
    def captured(self, monkeypatch):
        import aiosmtplib

        messages = []

        async def fake_send(message, **kwargs):
            messages.append(message)

        monkeypatch.setattr(aiosmtplib, "send", fake_send)
        monkeypatch.setenv("SMTP_HOST", "smtp.test.it")
        monkeypatch.setenv("EMAIL_FROM", "BandoFit <alerts@bandofit.it>")
        from app.core.config import get_settings

        get_settings.cache_clear()
        return messages

    async def test_sezioni_per_azienda(self, captured):
        await send_bandi_digest_email_multi(
            "dest@test.it",
            [
                {"azienda": "Alfa Srl", "bandi": [bando()]},
                {"azienda": "Beta Spa", "bandi": [bando(titolo="Altro bando")]},
            ],
            CTA,
            UNSUB,
        )
        [message] = captured
        # Oggetto e intestazione parlano di «aziende» (plurale), non «azienda».
        assert message["Subject"] == "2 nuovi bandi per le tue aziende — BandoFit"
        html_part = message.get_body(("html",)).get_content()
        text_part = message.get_body(("plain",)).get_content()
        assert "Alfa Srl" in html_part
        assert "Beta Spa" in html_part
        assert "Bando innovazione PMI" in html_part
        assert "Altro bando" in html_part
        # Il testo separa le sezioni con l'intestazione azienda.
        assert "[Alfa Srl]" in text_part
        assert "[Beta Spa]" in text_part

    async def test_sezioni_vuote_saltate(self, captured):
        await send_bandi_digest_email_multi(
            "dest@test.it",
            [
                {"azienda": "Alfa Srl", "bandi": [bando()]},
                {"azienda": "Beta Spa", "bandi": []},
            ],
            CTA,
            UNSUB,
        )
        html_part = captured[0].get_body(("html",)).get_content()
        assert "Alfa Srl" in html_part
        assert "Beta Spa" not in html_part  # nessun bando: sezione omessa


class TestDigestResend:
    async def test_payload_con_text_e_headers(self, monkeypatch):
        payloads = []

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            payloads.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "x"})

        transport = httpx.MockTransport(handler)
        real_client = httpx.AsyncClient

        def patched_client(*args, **kwargs):
            kwargs["transport"] = transport
            return real_client(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", patched_client)
        monkeypatch.setenv("RESEND_API_KEY", "rk")
        from app.core.config import get_settings

        get_settings.cache_clear()

        assert await send_bandi_digest_email("dest@test.it", [bando()], CTA, UNSUB)
        [payload] = payloads
        assert payload["text"].startswith("C'è un nuovo bando")
        assert payload["headers"]["List-Unsubscribe"] == f"<{UNSUB}>"
        assert payload["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


class TestFallbackLog:
    async def test_log_only_conta_come_inviata(self, caplog):
        with caplog.at_level(logging.INFO, logger="bandofit.email"):
            assert await send_bandi_digest_email("dest@test.it", [bando()], CTA, UNSUB)
        assert any("email dev fallback" in r.message for r in caplog.records)


class TestSanificazioneHeader:
    async def test_newline_negli_header_rimossi(self, monkeypatch):
        import aiosmtplib

        messages = []

        async def fake_send(message, **kwargs):
            messages.append(message)

        monkeypatch.setattr(aiosmtplib, "send", fake_send)
        monkeypatch.setenv("SMTP_HOST", "smtp.test.it")
        from app.core.config import get_settings

        get_settings.cache_clear()
        await email_service._dispatch(
            "dest@test.it", "Oggetto", "<p>ciao</p>", "ciao",
            headers={"X-Test": "valore\r\ninjettato"},
        )
        # \r e \n diventano spazi: nessun newline sopravvive nell'header.
        assert "\n" not in messages[0]["X-Test"]
        assert messages[0]["X-Test"] == "valore  injettato"