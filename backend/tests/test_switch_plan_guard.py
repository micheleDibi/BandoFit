"""Guard sui piani «su richiesta»: non attivabili self-serve (cambio piano e
registrazione), ma assegnabili dall'admin (self_serve=False)."""

from types import SimpleNamespace

import pytest

from app.core.errors import BadRequestError
from app.services import auth_service, user_service


class FakeQuery:
    def __init__(self, owner, table: str):
        self._owner = owner
        self._table = table
        self.filters: dict = {}

    def select(self, *args, **kwargs):
        return self

    def eq(self, column, value):
        self.filters[column] = value
        return self

    def limit(self, n):
        return self

    async def execute(self):
        self._owner.ops.append((self._table, "select", dict(self.filters)))
        return SimpleNamespace(data=self._owner.selects.get(self._table, []))


class FakeRpc:
    def __init__(self, owner, fn: str, params: dict):
        self._owner = owner
        self._fn = fn
        self._params = params

    async def execute(self):
        self._owner.rpc_calls.append((self._fn, self._params))
        return SimpleNamespace(data={})


class FakePrimary:
    def __init__(self, selects: dict | None = None):
        self.selects = selects or {}
        self.ops: list = []
        self.rpc_calls: list = []

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self, name)

    def rpc(self, fn: str, params: dict) -> FakeRpc:
        return FakeRpc(self, fn, params)


USER_ID = "11111111-2222-3333-4444-555555555555"
ME_SENTINEL = SimpleNamespace(plan_switch_adjustment=None)


@pytest.fixture(autouse=True)
def stub_post_switch(monkeypatch):
    """switch_plan dopo la RPC ricarica /me e pulisce gli inviti revocati:
    fuori scope per il guard, stubbati."""

    async def fake_get_me(primary, user_id):
        return ME_SENTINEL

    async def fake_cleanup(primary, adjustment):
        return None

    monkeypatch.setattr(user_service, "get_me", fake_get_me)
    monkeypatch.setattr(user_service.family_service, "cleanup_revoked_new_users", fake_cleanup)


def plans_with(tipo: str) -> dict:
    return {"subscription_plans": [{"tipo_prezzo": tipo}]}


class TestSwitchPlanGuard:
    async def test_su_richiesta_bloccato_senza_rpc(self):
        primary = FakePrimary(plans_with("su_richiesta"))
        with pytest.raises(BadRequestError):
            await user_service.switch_plan(primary, USER_ID, 5)
        assert primary.rpc_calls == []

    async def test_importo_e_gratis_passano(self):
        for tipo in ("importo", "gratis"):
            primary = FakePrimary(plans_with(tipo))
            me = await user_service.switch_plan(primary, USER_ID, 5)
            [(fn, params)] = primary.rpc_calls
            assert fn == "fn_switch_plan"
            assert params == {"p_user_id": USER_ID, "p_plan_id": 5}
            assert me is ME_SENTINEL

    async def test_admin_scavalca_il_guard(self):
        primary = FakePrimary(plans_with("su_richiesta"))
        await user_service.switch_plan(primary, USER_ID, 5, self_serve=False)
        [(fn, _)] = primary.rpc_calls
        assert fn == "fn_switch_plan"
        # Percorso admin: nessuna lookup del piano, si va dritti alla RPC.
        assert primary.ops == []

    async def test_piano_inesistente_prosegue_alla_rpc(self):
        # La mappatura errori (plan_not_available, ecc.) resta alla RPC.
        primary = FakePrimary({"subscription_plans": []})
        await user_service.switch_plan(primary, USER_ID, 999)
        assert len(primary.rpc_calls) == 1


class TestRegisterGuard:
    @pytest.fixture(autouse=True)
    def reset_cooldown(self):
        auth_service._last_sent.clear()
        yield
        auth_service._last_sent.clear()

    async def test_su_richiesta_respinto_senza_creare_utente(self):
        primary = FakePrimary(plans_with("su_richiesta"))
        create_calls: list = []
        primary.auth = SimpleNamespace(
            admin=SimpleNamespace(create_user=lambda payload: create_calls.append(payload))
        )
        with pytest.raises(BadRequestError):
            await auth_service.register(
                primary,
                email="Mario@Example.it",
                password="password-123",
                nome="Mario",
                cognome="Rossi",
                azienda=None,
                plan_slug="enterprise",
            )
        assert create_calls == []
        # Il rifiuto avviene PRIMA del cooldown: i 60s non vengono consumati.
        assert auth_service._last_sent == {}
        # La lookup usa lo slug del piano richiesto.
        [(_, _, filters)] = primary.ops
        assert filters["slug"] == "enterprise"
