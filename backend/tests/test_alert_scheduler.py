"""Scheduler degli alert: calcolo del prossimo tick (DST-safe), claim della
run per PK giorno e catch-up all'avvio."""

from datetime import date, datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from postgrest.exceptions import APIError

from app.services import alert_scheduler

ROMA = ZoneInfo("Europe/Rome")


def api_error(code: str) -> APIError:
    return APIError({"message": "dal db", "code": code, "details": None, "hint": None})


class FakeQuery:
    def __init__(self, owner):
        self._owner = owner
        self._payload = None

    def insert(self, payload):
        self._payload = payload
        return self

    async def execute(self):
        self._owner.inserts.append(self._payload)
        if self._owner.insert_error is not None:
            raise self._owner.insert_error
        return SimpleNamespace(data=[self._payload])


class FakePrimary:
    def __init__(self, insert_error: APIError | None = None):
        self.insert_error = insert_error
        self.inserts: list = []

    def table(self, name):
        assert name == "bando_alert_runs"
        return FakeQuery(self)


@pytest.fixture(autouse=True)
def stub_settings(monkeypatch):
    for key, value in {
        "PRIMARY_SUPABASE_URL": "https://dummy.supabase.co",
        "PRIMARY_SUPABASE_SERVICE_ROLE_KEY": "k",
        "SECONDARY_SUPABASE_URL": "https://d2.supabase.co",
        "SECONDARY_SUPABASE_ANON_KEY": "k",
        "ALERT_ORA_INVIO": "08:00",
    }.items():
        monkeypatch.setenv(key, value)
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestProssimaEsecuzione:
    def test_prima_dellora_di_invio(self):
        adesso = datetime(2026, 7, 13, 4, 0, tzinfo=timezone.utc)  # 06:00 a Roma
        prossima = alert_scheduler.prossima_esecuzione(adesso, "08:00", ROMA)
        assert prossima.astimezone(ROMA).strftime("%Y-%m-%d %H:%M") == "2026-07-13 08:00"

    def test_dopo_lora_di_invio(self):
        adesso = datetime(2026, 7, 13, 9, 0, tzinfo=timezone.utc)  # 11:00 a Roma
        prossima = alert_scheduler.prossima_esecuzione(adesso, "08:00", ROMA)
        assert prossima.astimezone(ROMA).strftime("%Y-%m-%d %H:%M") == "2026-07-14 08:00"

    def test_dst_marzo_orario_a_muro_stabile(self):
        """La notte del 29/03/2026 l'Italia passa all'ora legale: il tick
        resta alle 08:00 LOCALI (l'offset UTC cambia da +1 a +2)."""
        adesso = datetime(2026, 3, 28, 9, 0, tzinfo=ROMA)
        prossima = alert_scheduler.prossima_esecuzione(adesso, "08:00", ROMA)
        locale = prossima.astimezone(ROMA)
        assert locale.strftime("%Y-%m-%d %H:%M") == "2026-03-29 08:00"
        assert locale.utcoffset().total_seconds() == 2 * 3600


class TestClaimRun:
    async def test_claim_riuscito(self):
        primary = FakePrimary()
        assert await alert_scheduler.claim_run(primary, date(2026, 7, 13)) is True
        assert primary.inserts == [{"giorno": "2026-07-13"}]

    async def test_gia_eseguita(self):
        primary = FakePrimary(insert_error=api_error("23505"))
        assert await alert_scheduler.claim_run(primary, date(2026, 7, 13)) is False

    async def test_altro_errore_propaga(self):
        primary = FakePrimary(insert_error=api_error("42501"))
        with pytest.raises(APIError):
            await alert_scheduler.claim_run(primary, date(2026, 7, 13))


class TestEseguiSeDovuto:
    @pytest.fixture
    def run_calls(self, monkeypatch):
        calls: list[date] = []

        async def fake_run(primary, secondary, oggi):
            calls.append(oggi)
            return {"esito": "ok"}

        monkeypatch.setattr(alert_scheduler.bando_alert_service, "esegui_run", fake_run)
        return calls

    async def test_prima_dellora_non_esegue(self, run_calls):
        primary = FakePrimary()
        adesso = datetime(2026, 7, 13, 4, 0, tzinfo=timezone.utc)  # 06:00 a Roma
        assert await alert_scheduler.esegui_se_dovuto(primary, None, adesso) is None
        assert primary.inserts == []  # nemmeno il claim
        assert run_calls == []

    async def test_mezzanotte_locale_non_esegue(self, run_calls):
        """22:30 UTC = 00:30 del giorno DOPO a Roma: giorno nuovo ma prima
        delle 08:00 locali → niente run (il giorno è quello italiano)."""
        primary = FakePrimary()
        adesso = datetime(2026, 7, 13, 22, 30, tzinfo=timezone.utc)
        assert await alert_scheduler.esegui_se_dovuto(primary, None, adesso) is None
        assert run_calls == []

    async def test_claim_perso_non_esegue(self, run_calls):
        primary = FakePrimary(insert_error=api_error("23505"))
        adesso = datetime(2026, 7, 13, 10, 0, tzinfo=timezone.utc)  # 12:00 a Roma
        assert await alert_scheduler.esegui_se_dovuto(primary, None, adesso) is None
        assert run_calls == []

    async def test_catch_up_pomeridiano(self, run_calls):
        """Riavvio alle 15: la run di oggi manca → parte subito (catch-up)."""
        primary = FakePrimary()
        adesso = datetime(2026, 7, 13, 13, 0, tzinfo=timezone.utc)  # 15:00 a Roma
        out = await alert_scheduler.esegui_se_dovuto(primary, None, adesso)
        assert out == {"esito": "ok"}
        assert run_calls == [date(2026, 7, 13)]
