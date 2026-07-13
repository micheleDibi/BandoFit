"""Endpoint alert: disiscrizione pubblica (idempotente, anti-enumeration,
GET non mutante) e impostazioni col piano effettivo."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.api.routers import alerts
from app.services import bando_alert_service as svc

TOKEN = "12345678-0000-0000-0000-000000000050"
OWNER = "aaaaaaaa-0000-0000-0000-000000000051"
FIGLIO = "bbbbbbbb-0000-0000-0000-000000000052"


class FakeQuery:
    def __init__(self, owner, table):
        self._owner = owner
        self._table = table
        self._action = "select"
        self._payload = None
        self.filters: list = []

    def select(self, *args, **kwargs):
        return self

    def update(self, payload):
        self._action = "update"
        self._payload = payload
        return self

    def insert(self, payload):
        self._action = "insert"
        self._payload = payload
        return self

    def upsert(self, payload, **kwargs):
        self._action = "upsert"
        self._payload = payload
        return self

    def eq(self, column, value):
        self.filters.append(("eq", column, value))
        return self

    def in_(self, column, values):
        self.filters.append(("in", column, list(values)))
        return self

    def limit(self, n):
        return self

    async def execute(self):
        self._owner.ops.append((self._table, self._action, self._payload, list(self.filters)))
        if self._action == "select":
            return SimpleNamespace(data=self._owner.selects.get(self._table, []))
        if self._action == "update":
            return SimpleNamespace(data=self._owner.updates.get(self._table, []))
        return SimpleNamespace(data=[self._payload])


class FakePrimary:
    def __init__(self, selects: dict | None = None, updates: dict | None = None):
        self.selects = selects or {}
        self.updates = updates or {}
        self.ops: list = []

    def table(self, name):
        return FakeQuery(self, name)


@pytest.fixture(autouse=True)
def stub_settings(monkeypatch):
    for key, value in {
        "PRIMARY_SUPABASE_URL": "https://dummy.supabase.co",
        "PRIMARY_SUPABASE_SERVICE_ROLE_KEY": "k",
        "SECONDARY_SUPABASE_URL": "https://d2.supabase.co",
        "SECONDARY_SUPABASE_ANON_KEY": "k",
        "FRONTEND_URL": "https://app.test.it",
    }.items():
        monkeypatch.setenv(key, value)
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def make_client(primary: FakePrimary) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(alerts.router, prefix="/api/v1")
    app.state.primary = primary
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


class TestUnsubscribePubblico:
    async def test_one_click_204_e_update(self):
        primary = FakePrimary(updates={"bando_alert_settings": [{"user_id": OWNER}]})
        async with make_client(primary) as client:
            resp = await client.post(f"/api/v1/alerts/unsubscribe?token={TOKEN}")
        assert resp.status_code == 204
        update = next(op for op in primary.ops if op[1] == "update")
        assert update[2] == {"abilitati": False}
        assert ("eq", "unsubscribe_token", TOKEN) in update[3]
        # Audit best-effort quando il token corrisponde a un utente.
        assert any(op[0] == "audit_log" for op in primary.ops)

    async def test_token_malformato_stessa_risposta_nessuna_query(self):
        primary = FakePrimary()
        async with make_client(primary) as client:
            resp = await client.post("/api/v1/alerts/unsubscribe?token=<script>")
        assert resp.status_code == 204
        assert primary.ops == []

    async def test_token_ignoto_stessa_risposta(self):
        primary = FakePrimary(updates={"bando_alert_settings": []})
        async with make_client(primary) as client:
            resp = await client.post(f"/api/v1/alerts/unsubscribe?token={TOKEN}")
        assert resp.status_code == 204
        assert not any(op[0] == "audit_log" for op in primary.ops)

    async def test_form_browser_riceve_conferma_html(self):
        primary = FakePrimary(updates={"bando_alert_settings": [{"user_id": OWNER}]})
        async with make_client(primary) as client:
            resp = await client.post(
                f"/api/v1/alerts/unsubscribe?token={TOKEN}",
                headers={"accept": "text/html,application/xhtml+xml"},
            )
        assert resp.status_code == 200
        assert "Avvisi disattivati" in resp.text
        assert "https://app.test.it/app/preferenze" in resp.text

    async def test_get_non_muta(self):
        primary = FakePrimary()
        async with make_client(primary) as client:
            resp = await client.get(f"/api/v1/alerts/unsubscribe?token={TOKEN}")
        assert resp.status_code == 200
        assert "Disattiva gli avvisi" in resp.text  # bottone-form POST
        assert primary.ops == []  # NON mutante: gli scanner pre-aprono i GET


def sub_row(alert_attivo: bool = True, ritardo: int | None = 1) -> dict:
    return {
        "id": "99999999-0000-0000-0000-000000000053",
        "status": "active",
        "data_inizio": "2026-01-01",
        "data_scadenza": "2027-01-01",
        "subscription_plans": {
            "id": 4,
            "nome": "Advisor",
            "slug": "advisor",
            "prezzo_annuale": "699.00",
            "ai_check": 100,
            "alert_attivo": alert_attivo,
            "alert_giorni_preavviso": 30 if alert_attivo else None,
            "alert_ritardo_giorni": ritardo,
            "num_account_aziendali": 10,
            "ordering": 3,
            "is_active": True,
        },
    }


class TestAlertSettingsForUser:
    async def test_titolare_con_piano_idoneo(self):
        primary = FakePrimary(
            selects={
                "bando_alert_settings": [],
                "family_members": [],
                "user_subscriptions": [sub_row()],
            }
        )
        out = await svc.alert_settings_for_user(primary, {"id": OWNER})
        assert out == {
            "abilitati": True,  # riga assente = abilitati
            "piano_include_alert": True,
            "ritardo_giorni": 1,
        }

    async def test_figlio_attivo_eredita_il_piano_del_titolare(self):
        primary = FakePrimary(
            selects={
                "bando_alert_settings": [
                    {"abilitati": False}
                ],
                "family_members": [
                    {"member_id": FIGLIO, "parent_id": OWNER, "status": "active"}
                ],
                "user_subscriptions": [sub_row(ritardo=7)],
            }
        )
        out = await svc.alert_settings_for_user(primary, {"id": FIGLIO})
        assert out["piano_include_alert"] is True
        assert out["ritardo_giorni"] == 7
        assert out["abilitati"] is False
        # Il piano è stato letto sul TITOLARE.
        sub_query = next(op for op in primary.ops if op[0] == "user_subscriptions")
        assert ("eq", "user_id", OWNER) in sub_query[3]

    async def test_piano_senza_alert(self):
        primary = FakePrimary(
            selects={
                "bando_alert_settings": [],
                "family_members": [],
                "user_subscriptions": [sub_row(ritardo=None)],
            }
        )
        out = await svc.alert_settings_for_user(primary, {"id": OWNER})
        assert out == {
            "abilitati": True,
            "piano_include_alert": False,
            "ritardo_giorni": None,
        }