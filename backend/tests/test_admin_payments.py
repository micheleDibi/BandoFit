"""Admin sul modulo pagamenti: cambio piano gratuito con motivazione+audit
attore, cancel dell'ordine in corso; viste acquisti e registro fatture (sola
lettura); anomalie."""

from types import SimpleNamespace

import pytest

from app.core.errors import ForbiddenError, NotFoundError
from app.services import admin_payment_service, family_service, user_service

ADMIN = "aaaaaaaa-0000-0000-0000-000000000001"
TARGET = "bbbbbbbb-0000-0000-0000-000000000002"


class FakeQuery:
    def __init__(self, fake, table):
        self.fake = fake
        self.table = table
        self.op = "select"
        self.payload = None
        self.filtri = []

    def select(self, *_a, **_k):
        return self

    def insert(self, payload):
        self.op = "insert"
        self.payload = payload
        return self

    def eq(self, c, v):
        self.filtri.append((c, v))
        return self

    def limit(self, *_a):
        return self

    def order(self, *_a, **_k):
        return self

    def _match(self, row):
        return all(str(row.get(c)) == str(v) for c, v in self.filtri)

    async def execute(self):
        righe = self.fake.righe.setdefault(self.table, [])
        if self.op == "insert":
            self.fake.inserts.append((self.table, self.payload))
            return SimpleNamespace(data=[{"id": 1, **self.payload}])
        return SimpleNamespace(data=[dict(r) for r in righe if self._match(r)])


class FakeRpc:
    def __init__(self, fake, fn, params):
        self.fake = fake
        self.fn = fn
        self.params = params

    async def execute(self):
        self.fake.rpc_calls.append((self.fn, self.params))
        return SimpleNamespace(data={"purchase_id": "np"})


class FakePrimary:
    def __init__(self, righe=None):
        self.righe = righe or {}
        self.inserts = []
        self.rpc_calls = []

    def table(self, name):
        return FakeQuery(self, name)

    def rpc(self, fn, params):
        return FakeRpc(self, fn, params)


class FakeRevolut:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.cancellati = []

    async def cancel_order(self, order_id):
        self.cancellati.append(order_id)
        return {"state": "cancelled"}


@pytest.fixture
def _no_membership(monkeypatch):
    async def none(_p, _u):
        return None
    monkeypatch.setattr(family_service, "get_membership", none)


@pytest.fixture
def _fake_get_me(monkeypatch):
    # admin_switch_user_plan costruisce un AdminUserOut da get_me: si stubba il
    # risultato finale per non ricostruire l'intero MeOut (fuori scope).
    async def fake(_p, uid):
        return SimpleNamespace(profile=SimpleNamespace(), subscription=None,
                               progettista=None)

    monkeypatch.setattr(user_service, "get_me", fake)
    monkeypatch.setattr(user_service, "AdminUserOut",
                        lambda **kw: SimpleNamespace(**kw))
    monkeypatch.setattr(user_service, "me_family_to_admin", lambda me: None)


class TestCambioAdmin:
    async def test_cambio_con_motivazione_e_attore(self, _no_membership, _fake_get_me):
        primary = FakePrimary({"profiles": [{"id": TARGET}]})
        await user_service.admin_switch_user_plan(
            primary, TARGET, 3, admin_id=ADMIN, motivazione="Cliente convenzionato",
            revolut=FakeRevolut(enabled=False),
        )
        fn, params = primary.rpc_calls[0]
        assert fn == "fn_registra_cambio_admin"
        assert params["p_admin_id"] == ADMIN
        assert params["p_motivazione"] == "Cliente convenzionato"

    async def test_cancella_l_ordine_in_corso_prima(self, _no_membership, _fake_get_me):
        primary = FakePrimary({
            "profiles": [{"id": TARGET}],
            "purchases": [{"user_id": TARGET, "status": "in_attesa",
                           "revolut_order_id": "ord-vivo"}],
        })
        revolut = FakeRevolut(enabled=True)
        await user_service.admin_switch_user_plan(
            primary, TARGET, 3, admin_id=ADMIN, motivazione="x", revolut=revolut,
        )
        assert revolut.cancellati == ["ord-vivo"]  # cancel PRIMA della RPC
        assert primary.rpc_calls[0][0] == "fn_registra_cambio_admin"

    async def test_utente_inesistente(self, _no_membership):
        with pytest.raises(NotFoundError):
            await user_service.admin_switch_user_plan(
                FakePrimary({"profiles": []}), TARGET, 3,
                admin_id=ADMIN, motivazione="x", revolut=FakeRevolut(False),
            )

    async def test_figlio_attivo_rifiutato(self, monkeypatch):
        async def active(_p, _u):
            return {"status": "active", "parent_id": "p"}
        monkeypatch.setattr(family_service, "get_membership", active)
        with pytest.raises(ForbiddenError):
            await user_service.admin_switch_user_plan(
                FakePrimary({"profiles": [{"id": TARGET}]}), TARGET, 3,
                admin_id=ADMIN, motivazione="x", revolut=FakeRevolut(False),
            )


class TestViste:
    async def test_anomalie_aperte_escludono_le_risolte(self):
        primary = FakePrimary({
            "audit_log": [
                {"id": 1, "action": "payments.orphan", "payload": {"revolut_order_id": "o1"},
                 "created_at": "2027-01-01"},
                {"id": 2, "action": "payments.orphan", "payload": {"revolut_order_id": "o2"},
                 "created_at": "2027-01-02"},
                {"id": 99, "action": "payments.orphan_resolved", "payload": {"audit_id": 1}},
            ],
        })
        out = await admin_payment_service.list_anomalies(primary, "aperta")
        ids = [a["audit_id"] for a in out["items"]]
        assert ids == [2]  # la 1 è risolta

    async def test_risolvi_anomalia_scrive_audit(self):
        primary = FakePrimary()
        await admin_payment_service.resolve_anomaly(primary, 5, ADMIN)
        tabella, payload = primary.inserts[0]
        assert tabella == "audit_log"
        assert payload["action"] == "payments.orphan_resolved"
        assert payload["payload"]["audit_id"] == 5
