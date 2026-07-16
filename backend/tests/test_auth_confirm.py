"""POST /auth/confirm: conferma dell'indirizzo + scelta della password.

È il cammino senza cui nessun account registrato diventa utilizzabile — la
registrazione non raccoglie più la password — e prima di questo file non era
coperto da nulla.

L'invariante che protegge: il token si consuma **dopo** l'update riuscito. Con
l'ordine inverso una password rifiutata brucerebbe il link e lascerebbe l'utente
fuori per sempre, per aver scelto una password debole.
"""

from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.core.errors import BadRequestError, NotFoundError, UpstreamError
from app.services import auth_service

USER_ID = "11111111-1111-1111-1111-111111111111"
TOKEN = "t" * 43


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    for key, value in {
        "PRIMARY_SUPABASE_URL": "https://dummy.supabase.co",
        "PRIMARY_SUPABASE_SERVICE_ROLE_KEY": "k",
        "SECONDARY_SUPABASE_URL": "https://d2.supabase.co",
        "SECONDARY_SUPABASE_ANON_KEY": "k",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class FakePrimary:
    def __init__(self, *, update_raises: Exception | None = None):
        self.update_calls: list[tuple] = []

        async def update_user_by_id(user_id, payload):
            self.update_calls.append((user_id, payload))
            if update_raises:
                raise update_raises
            return SimpleNamespace(user=SimpleNamespace(email="mario@test.it"))

        self.auth = SimpleNamespace(admin=SimpleNamespace(update_user_by_id=update_user_by_id))


@pytest.fixture()
def token_spia(monkeypatch):
    """Registra l'ordine delle operazioni sul token: è l'ordine, non il singolo
    passo, a essere l'invariante."""
    eventi: list[str] = []

    async def fake_peek(_primary, _token, _purpose):
        eventi.append("peek")
        return USER_ID

    async def fake_consume(_primary, _token, _purpose):
        eventi.append("consume")
        return USER_ID

    monkeypatch.setattr(auth_service.token_service, "peek", fake_peek)
    monkeypatch.setattr(auth_service.token_service, "consume", fake_consume)
    return eventi


class TestConfermaRiuscita:
    async def test_imposta_password_e_conferma_insieme(self, token_spia):
        primary = FakePrimary()

        esito = await auth_service.confirm_email(primary, TOKEN, "password-nuova")

        assert esito == {"email": "mario@test.it"}
        [(user_id, payload)] = primary.update_calls
        assert user_id == USER_ID
        assert payload == {"password": "password-nuova", "email_confirm": True}

    async def test_il_token_si_consuma_solo_dopo_l_update(self, token_spia):
        await auth_service.confirm_email(FakePrimary(), TOKEN, "password-nuova")
        assert token_spia == ["peek", "consume"]


class TestConfermaRespinta:
    async def test_token_invalido_404_senza_toccare_l_utente(self, monkeypatch):
        async def peek_nulla(*_a):
            return None

        monkeypatch.setattr(auth_service.token_service, "peek", peek_nulla)
        primary = FakePrimary()

        with pytest.raises(NotFoundError):
            await auth_service.confirm_email(primary, TOKEN, "password-nuova")
        assert primary.update_calls == []

    async def test_password_rifiutata_non_brucia_il_link(self, token_spia):
        # Il punto dell'intero file: con consume-first, chi sceglie una password
        # debole resterebbe fuori con un link morto in mano.
        primary = FakePrimary(update_raises=Exception("Password should be at least 8 characters"))

        with pytest.raises(BadRequestError):
            await auth_service.confirm_email(primary, TOKEN, "corta")

        assert "consume" not in token_spia

    async def test_guasto_upstream_non_brucia_il_link(self, token_spia):
        primary = FakePrimary(update_raises=Exception("service unavailable"))

        with pytest.raises(UpstreamError):
            await auth_service.confirm_email(primary, TOKEN, "password-nuova")

        assert "consume" not in token_spia
