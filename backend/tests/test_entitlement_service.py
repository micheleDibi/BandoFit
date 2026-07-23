"""Snapshot entitlement lato backend: risoluzione dell'owner (collegato
attivo → titolare, pool condiviso), mapping difensivo dello snapshot RPC e
campi del membro (budget/consumi, WP6)."""

from types import SimpleNamespace

import pytest

from app.services import entitlement_service

USER_ID = "aaaaaaaa-0000-0000-0000-000000000001"
PARENT_ID = "bbbbbbbb-0000-0000-0000-000000000002"
USER = {"id": USER_ID}

SNAPSHOT = {
    "seats": {"base": 3, "extra": 2, "effettivo": 5, "usato": 2, "residuo": 3},
    "companies": {"base": 1, "extra": 0, "effettivo": 1, "usato": 1, "residuo": 0},
    "ai_checks": {"base": 20, "extra": 0, "effettivo": 20, "usato": 4, "residuo": 16,
                  "periodo_inizio": "2026-01-01", "periodo_fine": "2027-01-01"},
}


class FakeCountQuery:
    def __init__(self, count: int):
        self._count = count
        self.filters: dict = {}

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self.filters[col] = val
        return self

    def in_(self, col, vals):
        self.filters[f"{col}__in"] = list(vals)
        return self

    def gte(self, col, val):
        self.filters[f"{col}__gte"] = val
        return self

    def lt(self, col, val):
        self.filters[f"{col}__lt"] = val
        return self

    def limit(self, *a):
        return self

    async def execute(self):
        return SimpleNamespace(data=[], count=self._count)


class FakePrimary:
    def __init__(self, snapshot=SNAPSHOT, usati_membro: int = 0):
        self.snapshot = snapshot
        self.usati_membro = usati_membro
        self.rpcs: list = []
        self.count_queries: list[FakeCountQuery] = []

    def rpc(self, name: str, params: dict):
        self.rpcs.append((name, params))
        primary = self

        class _Rpc:
            async def execute(self_inner):
                return SimpleNamespace(data=primary.snapshot)

        return _Rpc()

    def table(self, name: str) -> FakeCountQuery:
        query = FakeCountQuery(self.usati_membro)
        self.count_queries.append(query)
        return query


@pytest.fixture
def membership(monkeypatch):
    holder = {"value": None}

    async def get_membership(primary, user_id):
        return holder["value"]

    # entitlement_service importa il simbolo direttamente: si patcha lì.
    monkeypatch.setattr(entitlement_service, "get_membership", get_membership)
    return holder


class TestGetEntitlements:
    async def test_titolare_snapshot_proprio(self, membership):
        primary = FakePrimary()
        out = await entitlement_service.get_entitlements(primary, USER)
        assert primary.rpcs == [("fn_entitlement_snapshot", {"p_user_id": USER_ID})]
        assert out.editable is True
        assert out.seats.effettivo == 5 and out.seats.extra == 2
        assert out.ai_checks.periodo_fine == "2027-01-01"
        # Campi del membro assenti per un titolare.
        assert out.ai_checks.budget_membro is None and out.ai_checks.usati_membro is None

    async def test_figlio_attivo_risolve_il_titolare_con_budget(self, membership):
        membership["value"] = {"id": "m-1", "status": "active", "parent_id": PARENT_ID,
                               "ai_check_budget": 5}
        primary = FakePrimary(usati_membro=2)
        out = await entitlement_service.get_entitlements(primary, USER)
        assert primary.rpcs == [("fn_entitlement_snapshot", {"p_user_id": PARENT_ID})]
        assert out.editable is False
        assert out.ai_checks.budget_membro == 5
        assert out.ai_checks.usati_membro == 2
        # Il conteggio è filtrato su membro + finestra del ciclo.
        [query] = primary.count_queries
        assert query.filters["user_id"] == USER_ID
        assert query.filters["family_parent_id"] == PARENT_ID
        assert query.filters["created_at__gte"] == "2026-01-01"
        assert query.filters["created_at__lt"] == "2027-01-02"

    async def test_figlio_illimitato(self, membership):
        membership["value"] = {"id": "m-1", "status": "active", "parent_id": PARENT_ID,
                               "ai_check_budget": None}
        primary = FakePrimary(usati_membro=9)
        out = await entitlement_service.get_entitlements(primary, USER)
        assert out.ai_checks.budget_membro is None  # illimitato
        assert out.ai_checks.usati_membro == 9

    async def test_figlio_retrocesso_resta_su_se_stesso(self, membership):
        membership["value"] = {"id": "m-1", "status": "demoted", "parent_id": PARENT_ID}
        primary = FakePrimary()
        out = await entitlement_service.get_entitlements(primary, USER)
        assert primary.rpcs == [("fn_entitlement_snapshot", {"p_user_id": USER_ID})]
        assert out.ai_checks.budget_membro is None

    async def test_snapshot_mancante_va_a_zero(self, membership):
        primary = FakePrimary(snapshot=None)
        out = await entitlement_service.get_entitlements(primary, USER)
        assert out.seats.effettivo == 0 and out.companies.usato == 0
        assert out.ai_checks.periodo_inizio is None
