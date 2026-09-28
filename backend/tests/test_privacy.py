"""Mascheramenti e HMAC per dominio (app/core/privacy.py).

I mascheramenti finiscono in log, request_meta e audit: il dato in chiaro non
deve mai sopravvivere, ma deve restare abbastanza per riconoscere un caso in
diagnostica. `hmac_dominio` deve essere stabile (serve a confrontare) e
separato per dominio (un identificativo di un contesto non si correla con
quello di un altro).
"""

import logging

import pytest

from app.core import privacy
from app.core.config import get_settings
from app.core.privacy import hmac_dominio, mask_cf, mask_email, mask_piva


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    for key, value in {
        "PRIMARY_SUPABASE_URL": "https://dummy.supabase.co",
        "PRIMARY_SUPABASE_SERVICE_ROLE_KEY": "k",
        "SECONDARY_SUPABASE_URL": "https://d2.supabase.co",
        "SECONDARY_SUPABASE_ANON_KEY": "k",
        "RATE_LIMIT_PEPPER": "pepe-di-test",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestMaschere:
    def test_piva(self):
        assert mask_piva("09876543217") == "098*****217"
        # le 5 cifre centrali non sopravvivono
        assert "76543" not in mask_piva("09876543217")
        assert mask_piva(" 09876543217 ") == "098*****217"

    def test_piva_corta_o_assente(self):
        assert mask_piva("12345") == "***"
        assert mask_piva(None) == "***"
        assert mask_piva("") == "***"

    def test_cf_persona_come_lo_storico(self):
        # Stesso formato del _mask_cf di openapi_service: i registri restano omogenei.
        assert mask_cf("RSSMRA85H04H501Z") == "RSSMRA*******01Z"
        assert mask_cf("rssmra85h04h501z") == "RSSMRA*******01Z"

    def test_cf_impresa_e_altro(self):
        assert mask_cf("09876543217") == "098*****217"
        assert mask_cf("ABC") == "***"
        assert mask_cf(None) == "***"

    def test_email(self):
        assert mask_email("mario.rossi@example.com") == "m***@example.com"
        assert "rossi" not in mask_email("mario.rossi@example.com")
        assert mask_email("non-una-email") == "***"
        assert mask_email("@example.com") == "***"
        assert mask_email(None) == "***"


class TestHmacDominio:
    def test_deterministico_esadecimale(self):
        primo = hmac_dominio("collegamenti", "09876543217")
        assert primo == hmac_dominio("collegamenti", "09876543217")
        assert len(primo) == 64
        int(primo, 16)  # esadecimale
        assert "09876543217" not in primo

    def test_diverso_per_dominio_e_per_valore(self):
        valore = "09876543217"
        assert hmac_dominio("collegamenti", valore) != hmac_dominio("pseudonimi", valore)
        assert hmac_dominio("collegamenti", valore) != hmac_dominio("collegamenti", "01122334459")

    def test_nessuna_collisione_per_concatenazione(self):
        # "a" + "b:c" e "a:b" + "c" non devono dare lo stesso digest.
        assert hmac_dominio("a", "b:c") != hmac_dominio("a:b", "c")

    def test_non_coincide_con_i_bucket_del_rate_limit(self):
        # La chiave è derivata dal pepper, non il pepper grezzo.
        from app.services import rate_limit_service

        bucket = rate_limit_service.bucket("ip", "203.0.113.7").split(":", 1)[1]
        assert not hmac_dominio("ip", "203.0.113.7").startswith(bucket)

    def test_il_pepper_cambia_il_digest(self, monkeypatch):
        prima = hmac_dominio("collegamenti", "x")
        monkeypatch.setenv("RATE_LIMIT_PEPPER", "un-altro-pepe")
        get_settings.cache_clear()
        assert hmac_dominio("collegamenti", "x") != prima

    def test_dominio_obbligatorio(self):
        with pytest.raises(ValueError):
            hmac_dominio("", "x")

    def test_senza_pepper_chiave_di_sviluppo_e_un_solo_avviso(self, monkeypatch, caplog):
        monkeypatch.setenv("RATE_LIMIT_PEPPER", "")
        monkeypatch.setattr(privacy, "_avviso_pepper_emesso", False)
        get_settings.cache_clear()
        with caplog.at_level(logging.WARNING, logger="bandofit.privacy"):
            primo = hmac_dominio("collegamenti", "x")
            secondo = hmac_dominio("collegamenti", "x")
        assert primo == secondo
        avvisi = [r for r in caplog.records if "rate_limit_pepper" in r.getMessage()]
        assert len(avvisi) == 1
