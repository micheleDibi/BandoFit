"""Snapshot entitlement lato backend: risoluzione dell'owner (collegato
attivo → titolare, pool condiviso), mapping difensivo dello snapshot RPC,
campi del membro (budget/consumi, WP6) e limiti del modulo partenariati
(0036/0037: solo a flag acceso, None se la RPC fallisce)."""

import logging
from types import SimpleNamespace

import pytest
from postgrest.exceptions import APIError

from app.core.config import get_settings
from app.schemas.entitlement import EntitlementsOut
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

# Forma di fn_partenariati_snapshot (0037): piano Pro con una call pubblicata.
PARTENARIATI = {
    "call_attive": {"limite": 3, "usate": 1, "residuo": 2},
    "candidature_mese": {"limite": 20, "usate": 0, "residuo": 20,
                         "periodo_inizio": "2026-09-01", "periodo_fine": "2026-09-30"},
}

_OBBLIGATORIE = {
    "PRIMARY_SUPABASE_URL": "https://dummy.supabase.co",
    "PRIMARY_SUPABASE_SERVICE_ROLE_KEY": "k",
    "SECONDARY_SUPABASE_URL": "https://d2.supabase.co",
    "SECONDARY_SUPABASE_ANON_KEY": "k",
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
    def __init__(self, snapshot=SNAPSHOT, usati_membro: int = 0,
                 partenariati=PARTENARIATI, errore_partenariati: Exception | None = None):
        self.snapshot = snapshot
        self.usati_membro = usati_membro
        self.partenariati = partenariati
        self.errore_partenariati = errore_partenariati
        self.rpcs: list = []
        self.count_queries: list[FakeCountQuery] = []

    def rpc(self, name: str, params: dict):
        self.rpcs.append((name, params))
        primary = self

        class _Rpc:
            async def execute(self_inner):
                if name == "fn_partenariati_snapshot":
                    if primary.errore_partenariati is not None:
                        raise primary.errore_partenariati
                    return SimpleNamespace(data=primary.partenariati)
                return SimpleNamespace(data=primary.snapshot)

        return _Rpc()

    def table(self, name: str) -> FakeCountQuery:
        query = FakeCountQuery(self.usati_membro)
        self.count_queries.append(query)
        return query


@pytest.fixture(autouse=True)
def flag(monkeypatch):
    """Flag dei partenariati via ambiente (vince sul .env): spento di default,
    `flag(True)` lo accende."""

    def imposta(valore: bool) -> None:
        for chiave, v in _OBBLIGATORIE.items():
            monkeypatch.setenv(chiave, v)
        monkeypatch.setenv("PARTENARIATI_ATTIVO", "true" if valore else "false")
        get_settings.cache_clear()

    imposta(False)
    yield imposta
    get_settings.cache_clear()


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


class TestPartenariati:
    async def test_flag_spento_nessuna_rpc(self, membership):
        primary = FakePrimary()
        out = await entitlement_service.get_entitlements(primary, USER)
        assert out.partenariati is None
        assert [nome for nome, _ in primary.rpcs] == ["fn_entitlement_snapshot"]
        # La chiave c'è comunque, a null: il frontend non la tratta come assente.
        assert out.model_dump(mode="json")["partenariati"] is None

    async def test_flag_acceso_titolare(self, membership, flag):
        flag(True)
        primary = FakePrimary()
        out = await entitlement_service.get_entitlements(primary, USER)
        assert primary.rpcs[-1] == ("fn_partenariati_snapshot", {"p_owner": USER_ID})
        assert out.partenariati.model_dump() == PARTENARIATI
        # Le altre risorse non cambiano.
        assert out.seats.effettivo == 5 and out.ai_checks.usato == 4

    async def test_flag_acceso_collegato_legge_il_pool_del_titolare(self, membership, flag):
        flag(True)
        membership["value"] = {"id": "m-1", "status": "active", "parent_id": PARENT_ID,
                               "ai_check_budget": None}
        primary = FakePrimary()
        out = await entitlement_service.get_entitlements(primary, USER)
        assert primary.rpcs[-1] == ("fn_partenariati_snapshot", {"p_owner": PARENT_ID})
        assert out.editable is False and out.partenariati.call_attive.limite == 3

    async def test_illimitato_e_non_incluso(self, membership, flag):
        flag(True)
        primary = FakePrimary(partenariati={
            "call_attive": {"limite": None, "usate": 4, "residuo": None},
            "candidature_mese": {"limite": 0, "usate": 0, "residuo": 0,
                                 "periodo_inizio": "2026-09-01", "periodo_fine": "2026-09-30"},
        })
        out = await entitlement_service.get_entitlements(primary, USER)
        # None = illimitato resta None (mai 0), 0 = non incluso resta 0.
        assert out.partenariati.call_attive.limite is None
        assert out.partenariati.call_attive.residuo is None
        assert out.partenariati.candidature_mese.limite == 0

    @pytest.mark.parametrize("errore", [
        APIError({"message": "Could not find the function public.fn_partenariati_snapshot",
                  "code": "PGRST202", "details": None, "hint": None}),
        TimeoutError(),
    ])
    async def test_errore_della_rpc_vale_none(self, membership, flag, caplog, errore):
        flag(True)
        primary = FakePrimary(errore_partenariati=errore)
        with caplog.at_level(logging.WARNING, logger="bandofit.entitlements"):
            out = await entitlement_service.get_entitlements(primary, USER)
        assert out.partenariati is None
        # Il resto dello snapshot arriva intatto.
        assert out.seats.effettivo == 5 and out.ai_checks.residuo == 16
        assert "limiti di partenariato" in caplog.text

    @pytest.mark.parametrize("dati", [
        None,
        {},
        {"call_attive": {"limite": 1, "usate": 0, "residuo": 1}},
        {"call_attive": {"limite": "tanti", "usate": 0, "residuo": 1},
         "candidature_mese": {"limite": 5, "usate": 0, "residuo": 5}},
    ])
    async def test_forma_inattesa_vale_none(self, membership, flag, dati):
        flag(True)
        out = await entitlement_service.get_entitlements(FakePrimary(partenariati=dati), USER)
        assert out.partenariati is None

    def test_default_dello_schema(self):
        out = EntitlementsOut(editable=True, seats=SNAPSHOT["seats"],
                              companies=SNAPSHOT["companies"], ai_checks=SNAPSHOT["ai_checks"])
        assert out.partenariati is None
