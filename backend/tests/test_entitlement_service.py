"""Snapshot entitlement lato backend: risoluzione dell'owner (collegato
attivo → titolare, pool condiviso) e mapping difensivo dello snapshot RPC."""

from types import SimpleNamespace

import pytest

from app.services import entitlement_service, family_service

USER_ID = "aaaaaaaa-0000-0000-0000-000000000001"
PARENT_ID = "bbbbbbbb-0000-0000-0000-000000000002"
USER = {"id": USER_ID}

SNAPSHOT = {
    "seats": {"base": 3, "extra": 2, "effettivo": 5, "usato": 2, "residuo": 3},
    "companies": {"base": 1, "extra": 0, "effettivo": 1, "usato": 1, "residuo": 0},
    "ai_checks": {"base": 20, "extra": 0, "effettivo": 20, "usato": 4, "residuo": 16,
                  "periodo_inizio": "2026-01-01", "periodo_fine": "2027-01-01"},
}


class FakePrimary:
    def __init__(self, snapshot=SNAPSHOT):
        self.snapshot = snapshot
        self.rpcs: list = []

    def rpc(self, name: str, params: dict):
        self.rpcs.append((name, params))
        primary = self

        class _Rpc:
            async def execute(self_inner):
                return SimpleNamespace(data=primary.snapshot)

        return _Rpc()


@pytest.fixture
def membership(monkeypatch):
    holder = {"value": None}

    async def get_membership(primary, user_id):
        return holder["value"]

    monkeypatch.setattr(family_service, "get_membership", get_membership)
    return holder


class TestGetEntitlements:
    async def test_titolare_snapshot_proprio(self, membership):
        primary = FakePrimary()
        out = await entitlement_service.get_entitlements(primary, USER)
        assert primary.rpcs == [("fn_entitlement_snapshot", {"p_user_id": USER_ID})]
        assert out.editable is True
        assert out.seats.effettivo == 5 and out.seats.extra == 2
        assert out.ai_checks.periodo_fine == "2027-01-01"

    async def test_figlio_attivo_risolve_il_titolare(self, membership):
        membership["value"] = {"status": "active", "parent_id": PARENT_ID}
        primary = FakePrimary()
        out = await entitlement_service.get_entitlements(primary, USER)
        assert primary.rpcs == [("fn_entitlement_snapshot", {"p_user_id": PARENT_ID})]
        assert out.editable is False

    async def test_figlio_retrocesso_resta_su_se_stesso(self, membership):
        membership["value"] = {"status": "demoted", "parent_id": PARENT_ID}
        primary = FakePrimary()
        await entitlement_service.get_entitlements(primary, USER)
        assert primary.rpcs == [("fn_entitlement_snapshot", {"p_user_id": USER_ID})]

    async def test_snapshot_mancante_va_a_zero(self, membership):
        primary = FakePrimary(snapshot=None)
        out = await entitlement_service.get_entitlements(primary, USER)
        assert out.seats.effettivo == 0 and out.companies.usato == 0
        assert out.ai_checks.periodo_inizio is None
