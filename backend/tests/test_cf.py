"""Test del codice fiscale: validazione locale gratuita + flusso di verifica
a pagamento (idempotenza, registro consumi, salvataggio non verificato)."""

import pytest

from app.core.errors import AppError, OpenapiNotConfiguredError
from app.services import openapi_service
from app.services.codice_fiscale import is_valid_cf, normalize_cf
from tests.test_openapi_service import FakePrimary, USER

CF_OK = "RSSMRA80A01H501U"


class TestValidazioneLocale:
    def test_cf_validi(self):
        assert is_valid_cf(CF_OK)
        assert is_valid_cf("rssmra80a01h501u")  # minuscole normalizzate
        assert is_valid_cf(" RSSMRA80A01H501U ")

    def test_omocodia(self):
        # ultima cifra del giorno sostituita (1 → M) con checksum ricalcolato
        assert is_valid_cf("RSSMRA80A0MH501M")

    def test_rifiuti(self):
        assert not is_valid_cf(None)
        assert not is_valid_cf("")
        assert not is_valid_cf("RSSMRA80A01H501X")  # checksum errato
        assert not is_valid_cf("RSSMRA80Z01H501U")  # mese Z inesistente
        assert not is_valid_cf("12345678901")       # è una P.IVA
        assert not is_valid_cf("RSSMRA80A01H501")   # 15 caratteri

    def test_normalize(self):
        assert normalize_cf("  rssmra80a01h501u ") == CF_OK
        assert normalize_cf(None) == ""


def fake_openapi(valid=True, error: Exception | None = None, enabled=True, sandbox=False):
    from types import SimpleNamespace

    async def verifica_cf(cf):
        if error:
            raise error
        return valid

    return SimpleNamespace(enabled=enabled, sandbox=sandbox, verifica_cf=verifica_cf)


PROFILE_EMPTY = {"codice_fiscale": None, "cf_verified_at": None}


class TestVerificaCf:
    async def test_formato_invalido_niente_chiamata(self):
        primary = FakePrimary()
        with pytest.raises(AppError) as exc:
            await openapi_service.verify_cf(primary, fake_openapi(), USER, "XXX")
        assert exc.value.code == "cf_invalid"
        assert primary.ops == []  # nessuna query, nessuna spesa

    async def test_idempotente_se_gia_verificato(self):
        primary = FakePrimary(
            selects={"profiles": [{"codice_fiscale": CF_OK, "cf_verified_at": "2026-07-01T00:00:00+00:00"}]}
        )
        called = []

        async def verifica_cf(cf):
            called.append(cf)

        from types import SimpleNamespace

        openapi = SimpleNamespace(enabled=True, sandbox=False, verifica_cf=verifica_cf)
        result = await openapi_service.verify_cf(primary, openapi, USER, CF_OK.lower())
        assert result["cf_verified_at"] == "2026-07-01T00:00:00+00:00"
        assert called == []  # nessuna chiamata a pagamento

    async def test_non_configurato(self):
        primary = FakePrimary(selects={"profiles": [PROFILE_EMPTY]})
        with pytest.raises(OpenapiNotConfiguredError):
            await openapi_service.verify_cf(primary, fake_openapi(enabled=False), USER, CF_OK)

    async def test_verifica_positiva_salva_cf_e_marca(self):
        primary = FakePrimary(selects={"profiles": [PROFILE_EMPTY]})
        result = await openapi_service.verify_cf(primary, fake_openapi(valid=True), USER, CF_OK)
        assert result["codice_fiscale"] == CF_OK
        assert result["cf_verified_at"] is not None
        update = primary.ops_for("profiles", "update")[0]
        assert update["codice_fiscale"] == CF_OK and update["cf_verified_at"]
        event = primary.ops_for("api_usage_events", "insert")[0]
        assert event["service"] == "IT-verifica_cf"
        assert event["outcome"] == "success" and event["cost_cents"] == 5
        # il CF nel registro è mascherato
        assert CF_OK not in str(event["request_meta"])

    async def test_verifica_negativa_salva_non_verificato(self):
        primary = FakePrimary(selects={"profiles": [PROFILE_EMPTY]})
        with pytest.raises(AppError) as exc:
            await openapi_service.verify_cf(primary, fake_openapi(valid=False), USER, CF_OK)
        assert exc.value.code == "cf_not_valid"
        update = primary.ops_for("profiles", "update")[0]
        assert update == {"codice_fiscale": CF_OK}  # senza marca di verifica
        event = primary.ops_for("api_usage_events", "insert")[0]
        assert event["outcome"] == "success"  # la chiamata è avvenuta (e costata)

    async def test_sandbox_costo_zero(self):
        primary = FakePrimary(selects={"profiles": [PROFILE_EMPTY]})
        await openapi_service.verify_cf(
            primary, fake_openapi(valid=True, sandbox=True), USER, CF_OK
        )
        assert primary.ops_for("api_usage_events", "insert")[0]["cost_cents"] == 0
