"""subscription_mgmt_service: downgrade/disdetta differiti, auto-renew,
metodo di pagamento (0-amount e revoca)."""

import uuid
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.services import subscription_mgmt_service as mgmt

USER = "00000000-0000-0000-0000-000000000001"

PIANI = {
    "gratuito": {"id": 1, "slug": "gratuito", "nome": "Gratuito", "ordering": 1,
                 "is_active": True},
    "smart": {"id": 2, "slug": "smart", "nome": "Smart", "ordering": 2, "is_active": True},
    "pro": {"id": 3, "slug": "pro", "nome": "Pro", "ordering": 3, "prezzo_annuale": "299.00",
            "tipo_prezzo": "importo", "is_active": True},
}


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

    def update(self, payload):
        self.op = "update"
        self.payload = payload
        return self

    def eq(self, c, v):
        self.filtri.append((c, v))
        return self

    def limit(self, *_a):
        return self

    def _match(self, row):
        return all(str(row.get(c)) == str(v) for c, v in self.filtri)

    def _con_embed(self, row):
        """Risolve l'embed PostgREST subscription_plans:to_plan_id(...) come fa
        il DB reale, così _cambio_programmato legge slug/nome della destinazione."""
        row = dict(row)
        if self.table == "scheduled_plan_changes" and row.get("to_plan_id"):
            piano = next((p for p in self.fake.righe.get("subscription_plans", [])
                          if p["id"] == row["to_plan_id"]), None)
            if piano:
                row["subscription_plans"] = {"slug": piano["slug"], "nome": piano["nome"]}
        return row

    async def execute(self):
        righe = self.fake.righe.setdefault(self.table, [])
        if self.op == "insert":
            riga = {"id": str(uuid.uuid4()), **self.payload}
            # Default DB non presenti nel payload (li applica Postgres).
            if self.table == "scheduled_plan_changes":
                riga.setdefault("status", "programmato")
            righe.append(riga)
            self.fake.inserts.append((self.table, riga))
            return SimpleNamespace(data=[riga])
        if self.op == "update":
            tocc = [r for r in righe if self._match(r)]
            for r in tocc:
                r.update({k: v for k, v in self.payload.items() if v != "now()"})
            self.fake.updates.append((self.table, self.payload, list(self.filtri)))
            return SimpleNamespace(data=tocc)
        return SimpleNamespace(data=[self._con_embed(r) for r in righe if self._match(r)])


class FakePrimary:
    def __init__(self, righe):
        self.righe = righe
        self.inserts = []
        self.updates = []

    def table(self, name):
        return FakeQuery(self, name)


class FakeRevolut:
    def __init__(self):
        self.ordini = []

    async def create_customer(self, email, full_name=None):
        return {"id": "cust-1"}

    async def create_order(self, **kwargs):
        self.ordini.append(kwargs)
        return {"id": "ord-0", "token": "tok-0"}


def _primary(*, plan="pro", giorni=100, auto_renew=False, metodo=True, scheduled=None):
    scadenza = (date.today() + timedelta(days=giorni)).isoformat()
    return FakePrimary({
        "user_subscriptions": [{
            "user_id": USER, "status": "active", "plan_id": PIANI[plan]["id"],
            "data_scadenza": scadenza, "auto_renew": auto_renew,
            "subscription_plans": PIANI[plan],
        }],
        "subscription_plans": list(PIANI.values()),
        "revolut_customers": [{
            "user_id": USER, "revolut_customer_id": "cust-1",
            "saved_method_id": "pm-1" if metodo else None,
            "saved_method_label": "•••• 4242" if metodo else None,
        }],
        "scheduled_plan_changes": scheduled or [],
        "profiles": [{"id": USER, "email": "u@x.it"}],
    })


class TestDowngrade:
    async def test_programma_disdetta(self):
        primary = _primary(plan="pro", giorni=100)
        out = await mgmt.programma_downgrade(primary, USER, "gratuito")
        assert out.cambio_programmato.to_plan_slug == "gratuito"
        assert out.cambio_programmato.motivo == "disdetta"
        riga = primary.righe["scheduled_plan_changes"][0]
        assert riga["effective_date"] == (date.today() + timedelta(days=100)).isoformat()

    async def test_downgrade_verso_piano_intermedio(self):
        primary = _primary(plan="pro")
        out = await mgmt.programma_downgrade(primary, USER, "smart")
        assert out.cambio_programmato.motivo == "downgrade"

    async def test_verso_piano_superiore_rifiutato(self):
        primary = _primary(plan="smart")
        with pytest.raises(BadRequestError, match="scendere"):
            await mgmt.programma_downgrade(primary, USER, "pro")

    async def test_sostituisce_un_programmato_precedente(self):
        primary = _primary(plan="pro", scheduled=[{
            "id": "old", "user_id": USER, "status": "programmato",
            "to_plan_id": 2, "effective_date": "2027-01-01",
        }])
        await mgmt.programma_downgrade(primary, USER, "gratuito")
        vecchio = next(r for r in primary.righe["scheduled_plan_changes"] if r["id"] == "old")
        assert vecchio["status"] == "annullato"

    async def test_scaduto_non_programmabile(self):
        primary = _primary(plan="pro", giorni=-1)
        with pytest.raises(ConflictError):
            await mgmt.programma_downgrade(primary, USER, "gratuito")


class TestAnnullo:
    async def test_annulla(self):
        primary = _primary(scheduled=[{
            "id": "s1", "user_id": USER, "status": "programmato",
            "to_plan_id": 1, "effective_date": "2027-06-01",
        }])
        await mgmt.annulla_cambio_programmato(primary, USER)
        assert primary.righe["scheduled_plan_changes"][0]["status"] == "annullato"

    async def test_niente_da_annullare(self):
        with pytest.raises(NotFoundError):
            await mgmt.annulla_cambio_programmato(_primary(), USER)


class TestAutoRenew:
    async def test_on_richiede_metodo(self):
        with pytest.raises(ConflictError, match="metodo"):
            await mgmt.imposta_auto_renew(_primary(metodo=False), USER, True)

    async def test_on_con_metodo(self):
        primary = _primary(metodo=True)
        out = await mgmt.imposta_auto_renew(primary, USER, True)
        assert out.auto_renew is True

    async def test_off_sempre_possibile(self):
        primary = _primary(auto_renew=True, metodo=False)
        out = await mgmt.imposta_auto_renew(primary, USER, False)
        assert out.auto_renew is False


class TestMetodo:
    async def test_aggiunta_crea_ordine_zero(self):
        primary = _primary()
        revolut = FakeRevolut()
        out = await mgmt.avvia_aggiunta_metodo(primary, revolut, USER, "u@x.it")
        assert out.revolut_order_token == "tok-0"
        assert revolut.ordini[0]["amount_cents"] == 0
        assert revolut.ordini[0]["metadata"]["scopo"] == "add_method"

    async def test_revoca_spegne_auto_renew_ma_non_la_grazia(self):
        primary = _primary(auto_renew=True, metodo=True)
        # grace_until presente: la revoca NON deve toccarlo
        primary.righe["user_subscriptions"][0]["grace_until"] = "2027-12-31"
        out = await mgmt.revoca_metodo(primary, USER)
        assert out.auto_renew is False and out.metodo.presente is False
        assert primary.righe["user_subscriptions"][0]["grace_until"] == "2027-12-31"
