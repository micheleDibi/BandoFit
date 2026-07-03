"""Test del servizio auth: cooldown anti-abuso sugli endpoint email pubblici."""

import pytest

from app.core.errors import ConflictError
from app.services import auth_service


class TestCooldown:
    @pytest.fixture(autouse=True)
    def reset_state(self):
        auth_service._last_sent.clear()
        yield
        auth_service._last_sent.clear()

    def test_prima_richiesta_passa(self):
        auth_service._check_cooldown("recover", "a@b.it")  # non solleva

    def test_richiesta_ravvicinata_bloccata(self):
        auth_service._check_cooldown("recover", "a@b.it")
        with pytest.raises(ConflictError):
            auth_service._check_cooldown("recover", "a@b.it")

    def test_email_diverse_indipendenti(self):
        auth_service._check_cooldown("recover", "a@b.it")
        auth_service._check_cooldown("recover", "c@d.it")  # non solleva

    def test_tipi_diversi_indipendenti(self):
        auth_service._check_cooldown("recover", "a@b.it")
        auth_service._check_cooldown("confirm", "a@b.it")  # non solleva

    def test_scaduto_il_cooldown_ripassa(self, monkeypatch):
        auth_service._check_cooldown("recover", "a@b.it")
        # simula il passare del tempo spostando indietro il timestamp registrato
        key = ("recover", "a@b.it")
        auth_service._last_sent[key] -= auth_service._COOLDOWN_SECONDS + 1
        auth_service._check_cooldown("recover", "a@b.it")  # non solleva
