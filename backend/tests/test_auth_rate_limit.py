"""Rate limiting degli endpoint auth (rate_limit_service + il gate di register).

Il limitatore è esso stesso una superficie d'attacco, quindi qui si asserisce
tanto su ciò che DEVE bloccare quanto su ciò che NON deve bloccare: il cap
globale (che se rifiutasse darebbe a un singolo IP l'interruttore delle
registrazioni di tutti) e il budget per-email (che deve fermare le email, mai
la creazione dell'account).
"""

from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.core.errors import RateLimitedError
from app.services import auth_service, rate_limit_service


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


class FakeRpc:
    """Primary che risponde all'RPC secondo una mappa bucket → consentito."""

    def __init__(self, verdetti: dict[str, bool] | None = None, raises: Exception | None = None):
        self.verdetti = verdetti or {}
        self.raises = raises
        self.calls: list[dict] = []

    def rpc(self, name: str, params: dict):
        self.calls.append({"name": name, **params})
        outer = self

        class _Q:
            async def execute(self):
                if outer.raises:
                    raise outer.raises
                return SimpleNamespace(data=outer.verdetti.get(params["p_bucket"], True))

        return _Q()


class TestBucket:
    def test_forma_e_determinismo(self):
        primo = rate_limit_service.bucket("ip", "203.0.113.7")
        assert primo == rate_limit_service.bucket("ip", "203.0.113.7")
        assert primo.startswith("ip:")
        assert len(primo.split(":", 1)[1]) == 32

    def test_il_valore_in_chiaro_non_compare_mai(self):
        # A DB non devono finire né indirizzi email né IP: la tabella non deve
        # essere un registro di dati personali né un dizionario attaccabile.
        assert "mario@test.it" not in rate_limit_service.bucket("email", "mario@test.it")
        assert "203.0.113.7" not in rate_limit_service.bucket("ip", "203.0.113.7")

    def test_scope_diversi_non_collidono(self):
        stesso_valore = "mario@test.it"
        assert rate_limit_service.bucket("ip", stesso_valore) != rate_limit_service.bucket(
            "email", stesso_valore
        )

    def test_il_pepper_cambia_il_digest(self, monkeypatch):
        senza = rate_limit_service.bucket("ip", "203.0.113.7")
        monkeypatch.setenv("RATE_LIMIT_PEPPER", "un-altro-pepe")
        get_settings.cache_clear()
        assert rate_limit_service.bucket("ip", "203.0.113.7") != senza


class TestAllow:
    async def test_passa_i_parametri_alla_rpc(self):
        primary = FakeRpc()
        await rate_limit_service.allow(primary, "ip:abc", 5, 900)
        assert primary.calls == [
            {"name": "fn_consume_auth_rate_limit", "p_bucket": "ip:abc", "p_limit": 5, "p_window_seconds": 900}
        ]

    async def test_riporta_il_verdetto(self):
        primary = FakeRpc({"ip:pieno": False})
        assert await rate_limit_service.allow(primary, "ip:pieno", 5, 900) is False
        assert await rate_limit_service.allow(primary, "ip:libero", 5, 900) is True

    async def test_fail_open_se_la_rpc_esplode(self, caplog):
        # Un database che fa i capricci non deve spegnere le registrazioni: si
        # passa e resta il log. Il rovescio (un attacco durante un guasto non
        # viene limitato) è il compromesso scelto.
        primary = FakeRpc(raises=Exception("connessione persa"))
        assert await rate_limit_service.allow(primary, "ip:abc", 5, 900) is True
        assert any("Rate limit non applicato" in r.message for r in caplog.records)


class TestGateIp:
    async def test_ip_ignoto_nessun_limite_per_ip(self):
        # Nessun IP attendibile → si contano solo i bucket globali. Meglio che
        # contare tutti su una chiave sola.
        primary = FakeRpc()
        await auth_service._gate_ip(primary, None)
        assert [c["p_bucket"] for c in primary.calls] == ["global"]

    async def test_burst_superato_blocca_con_429(self):
        bucket = rate_limit_service.bucket("ip", "203.0.113.7")
        primary = FakeRpc({bucket: False})
        with pytest.raises(RateLimitedError) as exc:
            await auth_service._gate_ip(primary, "203.0.113.7")
        assert exc.value.status_code == 429
        assert "qualche minuto" in exc.value.message

    async def test_il_limite_giornaliero_dice_il_vero_sui_tempi(self):
        # «Riprova tra qualche minuto» su una finestra di 24 ore manderebbe
        # l'utente a sbattere; e chi iscrive un team dietro un NAT deve avere
        # una via umana.
        daily = rate_limit_service.bucket("ip24", "203.0.113.7")
        primary = FakeRpc({daily: False})
        with pytest.raises(RateLimitedError) as exc:
            await auth_service._gate_ip(primary, "203.0.113.7")
        assert "minuto" not in exc.value.message
        assert "scrivici" in exc.value.message

    async def test_il_cap_globale_avvisa_ma_non_blocca(self, caplog):
        # Il punto politico dell'intero limitatore: se il globale bloccasse, un
        # singolo IP potrebbe spegnere la registrazione a tutti gli altri.
        primary = FakeRpc({"global": False})
        await auth_service._gate_ip(primary, "203.0.113.7")  # non solleva
        assert any("soglia globale" in r.message for r in caplog.records)

    async def test_il_globale_si_conta_prima_dell_ip(self):
        primary = FakeRpc()
        await auth_service._gate_ip(primary, "203.0.113.7")
        assert primary.calls[0]["p_bucket"] == "global"
        assert len(primary.calls) == 3  # global + burst + daily
