"""addon_inventory_service: inventario, grant (RPC + notifica), revoca,
mappatura errori RPC."""

from types import SimpleNamespace

import pytest
from postgrest.exceptions import APIError

from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.schemas.addon import AdminGrantAddonIn, AdminRevokeAddonIn
from app.services import addon_inventory_service, notification_service

USER = "00000000-0000-0000-0000-000000000001"
ADMIN = "00000000-0000-0000-0000-000000000009"


class FakeQuery:
    def __init__(self, owner, table):
        self.owner = owner
        self.table = table

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a):
        return self

    def gt(self, *_a):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a):
        return self

    async def execute(self):
        return SimpleNamespace(data=self.owner.rows.get(self.table, []))


class FakeRpc:
    def __init__(self, owner, fn, params):
        self.owner = owner
        self.fn = fn
        self.params = params

    async def execute(self):
        self.owner.rpc_calls.append((self.fn, self.params))
        err = self.owner.rpc_errors.get(self.fn)
        if err:
            raise err
        return SimpleNamespace(data=self.owner.rpc_results.get(self.fn, {}))


class FakePrimary:
    def __init__(self, rows=None):
        self.rows = rows or {}
        self.rpc_calls = []
        self.rpc_errors = {}
        self.rpc_results = {}

    def table(self, name):
        return FakeQuery(self, name)

    def rpc(self, fn, params):
        return FakeRpc(self, fn, params)


def api_error(details: str) -> APIError:
    return APIError({"message": "db", "code": "P0001", "details": details, "hint": None})


@pytest.fixture
def notify_calls(monkeypatch):
    calls = []

    async def fake(primary, user_ids, **kwargs):
        calls.append({"user_ids": [str(u) for u in user_ids], **kwargs})

    monkeypatch.setattr(notification_service, "notify", fake)
    return calls


class TestInventario:
    async def test_map_embed(self):
        primary = FakePrimary(rows={
            "user_addon_inventory": [{
                "addon_id": 5, "quantita": 3, "updated_at": "2027-01-01T00:00:00+00:00",
                "addons": {"slug": "consulto-esperto", "nome": "Consulto",
                           "tipo_fruizione": "consumabile"},
            }],
            "addon_ledger": [
                {"addon_id": 5, "tipo": "purchase", "delta": 4},
                {"addon_id": 5, "tipo": "consume", "delta": -1},
            ],
        })
        out = await addon_inventory_service.get_inventory(primary, USER)
        assert out[0].slug == "consulto-esperto" and out[0].quantita == 3
        assert out[0].tipo_fruizione == "consumabile"
        assert out[0].acquistate == 4 and out[0].consumate == 1

    async def test_include_esaurite_e_ignora_le_revoche_nei_consumi(self):
        # Dalla 0030 le voci a quantità 0 restano visibili («Esaurito») e le
        # revoche admin riducono acquistate ma NON contano come consumo.
        primary = FakePrimary(rows={
            "user_addon_inventory": [{
                "addon_id": 7, "quantita": 0, "updated_at": None,
                "addons": {"slug": "posti-extra", "nome": "Posti extra",
                           "tipo_fruizione": "consumabile", "risorsa": "seats"},
            }],
            "addon_ledger": [
                {"addon_id": 7, "tipo": "admin_grant", "delta": 2},
                {"addon_id": 7, "tipo": "admin_revoke", "delta": -2},
            ],
        })
        out = await addon_inventory_service.get_inventory(primary, USER)
        assert out[0].quantita == 0 and out[0].risorsa == "seats"
        assert out[0].acquistate == 2 and out[0].consumate == 0


class TestGrant:
    async def test_grant_felice_con_notifica(self, notify_calls):
        primary = FakePrimary(rows={"addons": [{"nome": "Consulto"}]})
        primary.rpc_results["fn_admin_grant_addon"] = {
            "purchase_id": "pur-1", "quantita_residua": 3}
        out = await addon_inventory_service.grant(
            primary, ADMIN, USER, AdminGrantAddonIn(addon_id=5, quantita=3, motivazione="Cortesia")
        )
        assert out.purchase_id == "pur-1" and out.quantita_residua == 3
        fn, params = primary.rpc_calls[0]
        assert fn == "fn_admin_grant_addon"
        assert params["p_motivazione"] == "Cortesia" and params["p_quantita"] == 3
        [n] = notify_calls
        assert n["user_ids"] == [USER] and n["dedup_key"] == "addon-grant:pur-1"

    async def test_notifica_che_esplode_non_fa_fallire_il_grant(self, monkeypatch):
        # La RPC è già committata: un guasto nel lookup nome/notifica non deve
        # far risalire un errore (un retry raddoppierebbe i consumabili).
        async def esplode(*_a, **_k):
            raise RuntimeError("db giù")

        monkeypatch.setattr(notification_service, "notify", esplode)
        primary = FakePrimary(rows={"addons": [{"nome": "Consulto"}]})
        primary.rpc_results["fn_admin_grant_addon"] = {
            "purchase_id": "pur-1", "quantita_residua": 2}
        out = await addon_inventory_service.grant(
            primary, ADMIN, USER, AdminGrantAddonIn(addon_id=5, quantita=2, motivazione="x")
        )
        assert out.quantita_residua == 2  # grant riuscito comunque

    async def test_grant_gia_posseduto(self):
        primary = FakePrimary()
        primary.rpc_errors["fn_admin_grant_addon"] = api_error("addon_gia_posseduto")
        with pytest.raises(ConflictError):
            await addon_inventory_service.grant(
                primary, ADMIN, USER, AdminGrantAddonIn(addon_id=5, motivazione="x")
            )

    async def test_grant_utente_inesistente(self):
        primary = FakePrimary()
        primary.rpc_errors["fn_admin_grant_addon"] = api_error("user_not_found")
        with pytest.raises(NotFoundError):
            await addon_inventory_service.grant(
                primary, ADMIN, USER, AdminGrantAddonIn(addon_id=5, motivazione="x")
            )

    def test_motivazione_obbligatoria_a_schema(self):
        # La motivazione vuota è respinta già dallo schema (min_length=1).
        with pytest.raises(Exception):
            AdminGrantAddonIn(addon_id=5, motivazione="")


class TestRevoke:
    async def test_revoke_clamp(self):
        primary = FakePrimary()
        primary.rpc_results["fn_admin_revoke_addon"] = {
            "quantita_revocata": 2, "quantita_residua": 0}
        out = await addon_inventory_service.revoke(
            primary, ADMIN, USER, 5, AdminRevokeAddonIn(quantita=5, motivazione="errore")
        )
        assert out.quantita_revocata == 2 and out.quantita_residua == 0

    async def test_revoke_niente_da_revocare(self):
        primary = FakePrimary()
        primary.rpc_errors["fn_admin_revoke_addon"] = api_error("niente_da_revocare")
        with pytest.raises(ConflictError):
            await addon_inventory_service.revoke(
                primary, ADMIN, USER, 5, AdminRevokeAddonIn(motivazione="x")
            )
