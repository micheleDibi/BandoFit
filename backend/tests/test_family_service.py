"""Test del family service: mappatura errori RPC, mapping dei membri,
fallback dell'email service."""

import logging

import pytest
from postgrest.exceptions import APIError

from app.core.errors import (
    AppError,
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UpstreamError,
)
from app.services.family_service import _RPC_ERRORS, map_member, raise_from_rpc


def rpc_error(detail: str) -> APIError:
    return APIError(
        {"message": "messaggio dal db", "code": "P0001", "details": detail, "hint": None}
    )


class TestRaiseFromRpc:
    EXPECTED_CLASSES = {
        "cannot_invite_self": BadRequestError,
        "not_family_parent": ForbiddenError,
        "parent_in_family": ForbiddenError,
        "target_is_admin": ConflictError,
        "target_is_parent": ConflictError,
        "already_in_family": ConflictError,
        "invite_already_pending": ConflictError,
        "family_limit_reached": ConflictError,
        "family_full": ConflictError,
        "invitation_not_found": NotFoundError,
        "member_not_found": NotFoundError,
        "child_plan_locked": ForbiddenError,
        "plan_not_available": BadRequestError,
    }

    @pytest.mark.parametrize("detail,expected", sorted(EXPECTED_CLASSES.items()))
    def test_codici_mappati(self, detail, expected):
        with pytest.raises(expected):
            raise_from_rpc(rpc_error(detail))

    def test_tutti_i_codici_della_mappa_sono_app_error(self):
        for error_cls, message in _RPC_ERRORS.values():
            assert issubclass(error_cls, AppError)
            assert message  # mai messaggi vuoti verso l'utente

    def test_codice_sconosciuto_diventa_upstream(self):
        with pytest.raises(UpstreamError):
            raise_from_rpc(rpc_error("qualcosa_di_nuovo"))

    def test_detail_mancante_diventa_upstream(self):
        with pytest.raises(UpstreamError):
            raise_from_rpc(
                APIError({"message": "boom", "code": "42883", "details": None, "hint": None})
            )


class TestMapMember:
    ROW = {
        "id": "11111111-1111-1111-1111-111111111111",
        "parent_id": "22222222-2222-2222-2222-222222222222",
        "member_id": "33333333-3333-3333-3333-333333333333",
        "denominazione": "Sede di Bari",
        "invited_email": "bari@azienda.it",
        "invite_kind": "new_user",
        "status": "pending",
        "invited_at": "2026-07-03T10:00:00+00:00",
        "joined_at": None,
        "demoted_at": None,
    }

    def test_mapping(self):
        member = map_member(self.ROW)
        assert member.email == "bari@azienda.it"
        assert member.status == "pending"
        assert member.joined_at is None

    def test_mapping_demoted(self):
        row = {
            **self.ROW,
            "status": "demoted",
            "joined_at": "2026-07-03T11:00:00+00:00",
            "demoted_at": "2026-07-04T09:00:00+00:00",
        }
        member = map_member(row)
        assert member.status == "demoted"
        assert member.demoted_at is not None


class TestEmailFallback:
    @pytest.fixture(autouse=True)
    def dummy_settings(self, monkeypatch):
        for key, value in {
            "PRIMARY_SUPABASE_URL": "https://dummy.supabase.co",
            "PRIMARY_SUPABASE_SERVICE_ROLE_KEY": "k",
            "SECONDARY_SUPABASE_URL": "https://d2.supabase.co",
            "SECONDARY_SUPABASE_ANON_KEY": "k",
            "RESEND_API_KEY": "",
            "SMTP_HOST": "",
        }.items():
            monkeypatch.setenv(key, value)
        from app.core.config import get_settings

        get_settings.cache_clear()
        yield
        get_settings.cache_clear()

    async def test_senza_api_key_logga_e_non_solleva(self, caplog):
        from app.services.email_service import send_family_invitation_email

        with caplog.at_level(logging.INFO, logger="bandofit.email"):
            ok = await send_family_invitation_email("a@b.it", "ACME Srl", "Sede 2")
        assert ok is True
        assert any("email dev fallback" in record.message for record in caplog.records)

    async def test_errore_http_non_solleva(self, monkeypatch):
        import httpx

        from app.services import email_service

        monkeypatch.setenv("RESEND_API_KEY", "re_test_123")
        from app.core.config import get_settings

        get_settings.cache_clear()

        sent_payloads = []

        def mock_transport_handler(request: httpx.Request) -> httpx.Response:
            sent_payloads.append(request)
            return httpx.Response(500, text="boom")

        original_client = httpx.AsyncClient

        def patched_client(**kwargs):
            kwargs["transport"] = httpx.MockTransport(mock_transport_handler)
            return original_client(**kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", patched_client)
        ok = await email_service.send_family_invitation_email("a@b.it", "ACME", "Sede")
        assert ok is False
        # payload conforme all'API Resend
        request = sent_payloads[0]
        assert request.headers["authorization"] == "Bearer re_test_123"
        import json

        body = json.loads(request.content)
        assert body["to"] == ["a@b.it"]
        assert "ACME" in body["subject"]

    async def test_smtp_ha_priorita_e_costruisce_il_messaggio(self, monkeypatch):
        import aiosmtplib

        from app.services import email_service

        monkeypatch.setenv("SMTP_HOST", "ssl0.ovh.net")
        monkeypatch.setenv("SMTP_PORT", "465")
        monkeypatch.setenv("SMTP_USER", "noreply@azienda.it")
        monkeypatch.setenv("SMTP_PASSWORD", "segreta")
        monkeypatch.setenv("EMAIL_FROM", "BandoFit <noreply@azienda.it>")
        monkeypatch.setenv("RESEND_API_KEY", "re_ignorata")  # SMTP vince
        from app.core.config import get_settings

        get_settings.cache_clear()

        sent = {}

        async def fake_send(message, **kwargs):
            sent["message"] = message
            sent["kwargs"] = kwargs

        monkeypatch.setattr(aiosmtplib, "send", fake_send)
        ok = await email_service.send_family_invitation_email("a@b.it", "ACME Srl", "Sede 2")
        assert ok is True
        assert sent["kwargs"]["hostname"] == "ssl0.ovh.net"
        assert sent["kwargs"]["port"] == 465
        assert sent["kwargs"]["use_tls"] is True  # 465 = TLS implicito
        assert sent["kwargs"]["start_tls"] is False
        message = sent["message"]
        assert message["To"] == "a@b.it"
        assert "ACME Srl" in message["Subject"]
        assert "noreply@azienda.it" in str(message["From"])
        # multipart: testo semplice + alternativa HTML
        parts = [p.get_content_type() for p in message.walk()]
        assert "text/plain" in parts and "text/html" in parts

    async def test_smtp_porta_587_usa_starttls(self, monkeypatch):
        import aiosmtplib

        from app.services import email_service

        monkeypatch.setenv("SMTP_HOST", "ssl0.ovh.net")
        monkeypatch.setenv("SMTP_PORT", "587")
        monkeypatch.setenv("SMTP_USER", "noreply@azienda.it")
        monkeypatch.setenv("SMTP_PASSWORD", "segreta")
        from app.core.config import get_settings

        get_settings.cache_clear()

        captured = {}

        async def fake_send(message, **kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(aiosmtplib, "send", fake_send)
        assert await email_service.send_family_invitation_email("a@b.it", "ACME", "Sede")
        assert captured["use_tls"] is False
        assert captured["start_tls"] is True

    async def test_errore_smtp_non_solleva(self, monkeypatch):
        import aiosmtplib

        from app.services import email_service

        monkeypatch.setenv("SMTP_HOST", "ssl0.ovh.net")
        monkeypatch.setenv("SMTP_USER", "u")
        monkeypatch.setenv("SMTP_PASSWORD", "p")
        from app.core.config import get_settings

        get_settings.cache_clear()

        async def fake_send(message, **kwargs):
            raise aiosmtplib.SMTPAuthenticationError(535, "auth failed")

        monkeypatch.setattr(aiosmtplib, "send", fake_send)
        ok = await email_service.send_family_invitation_email("a@b.it", "ACME", "Sede")
        assert ok is False
