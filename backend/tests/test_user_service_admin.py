"""Gestione utenti lato admin: cambio ruolo (promozione progettista via RPC,
demozioni con audit) e guardie anti auto-lockout."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from postgrest.exceptions import APIError

from app.core.errors import BadRequestError, NotFoundError
from app.schemas.user import AdminUserUpdate, MeOut, ProfileOut, ProgettistaOut
from app.services import user_service

ADMIN_ID = "aaaaaaaa-0000-0000-0000-000000000001"
TARGET_ID = UUID("bbbbbbbb-0000-0000-0000-000000000002")


class FakeQuery:
    def __init__(self, owner, table: str):
        self._owner = owner
        self._table = table
        self._action = "select"
        self._payload = None
        self.filters: dict = {}

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

    def eq(self, column, value):
        self.filters[column] = value
        return self

    def limit(self, n):
        return self

    async def execute(self):
        self._owner.ops.append((self._table, self._action, self._payload, dict(self.filters)))
        if self._action == "select":
            return SimpleNamespace(data=self._owner.selects.get(self._table, []))
        if self._action == "update":
            return SimpleNamespace(data=self._owner.updates.get(self._table, [{"id": "x"}]))
        return SimpleNamespace(data=[self._payload])


class FakeRpc:
    def __init__(self, owner, fn: str, params: dict):
        self._owner = owner
        self._fn = fn
        self._params = params

    async def execute(self):
        self._owner.rpc_calls.append((self._fn, self._params))
        error = self._owner.rpc_errors.get(self._fn)
        if error is not None:
            raise error
        return SimpleNamespace(data="PRG-00001")


class FakePrimary:
    def __init__(self, selects: dict | None = None, updates: dict | None = None):
        self.selects = selects or {}
        self.updates = updates or {}
        self.ops: list = []
        self.rpc_calls: list = []
        self.rpc_errors: dict = {}

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self, name)

    def rpc(self, fn: str, params: dict) -> FakeRpc:
        return FakeRpc(self, fn, params)


def make_me(role: str = "cliente", codice: str | None = None) -> MeOut:
    return MeOut(
        profile=ProfileOut(
            id=TARGET_ID,
            email="target@test.it",
            role=role,
            is_active=True,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        subscription=None,
        family=None,
        progettista=ProgettistaOut(codice=codice) if codice else None,
    )


@pytest.fixture(autouse=True)
def stub_get_me(monkeypatch):
    async def fake_get_me(primary, user_id):
        return make_me(role="progettista", codice="PRG-00001")

    monkeypatch.setattr(user_service, "get_me", fake_get_me)


def ops_for(primary: FakePrimary, table: str, action: str) -> list:
    return [op for op in primary.ops if op[0] == table and op[1] == action]


class TestFetchProgettista:
    """Parità admin (0019): il codice si espone anche agli admin, se la riga
    esiste (per loro arriva pigramente alla prima proposta)."""

    async def test_admin_con_codice(self):
        primary = FakePrimary(selects={"progettisti": [{"codice": "PRG-00007"}]})
        out = await user_service._fetch_progettista(primary, str(TARGET_ID), "admin")
        assert out == ProgettistaOut(codice="PRG-00007")

    async def test_admin_senza_codice(self):
        primary = FakePrimary(selects={"progettisti": []})
        out = await user_service._fetch_progettista(primary, str(TARGET_ID), "admin")
        assert out is None

    async def test_cliente_senza_query(self):
        primary = FakePrimary()
        out = await user_service._fetch_progettista(primary, str(TARGET_ID), "cliente")
        assert out is None
        assert primary.ops == []


class TestPromozioneProgettista:
    async def test_passa_dalla_rpc_e_non_tocca_profiles(self):
        primary = FakePrimary()
        out = await user_service.admin_update_user(
            primary, TARGET_ID, AdminUserUpdate(role="progettista"), ADMIN_ID
        )
        [(fn, params)] = primary.rpc_calls
        assert fn == "fn_promote_progettista"
        assert params == {"p_user_id": str(TARGET_ID), "p_actor_id": ADMIN_ID}
        assert ops_for(primary, "profiles", "update") == []
        # L'audit della promozione lo scrive la RPC: nessun doppione dal backend.
        assert ops_for(primary, "audit_log", "insert") == []
        assert out.progettista is not None and out.progettista.codice == "PRG-00001"

    async def test_con_sospensione_applica_entrambe(self):
        primary = FakePrimary()
        await user_service.admin_update_user(
            primary,
            TARGET_ID,
            AdminUserUpdate(role="progettista", is_active=False),
            ADMIN_ID,
        )
        assert len(primary.rpc_calls) == 1
        [(_, _, payload, filters)] = ops_for(primary, "profiles", "update")
        assert payload == {"is_active": False}
        assert filters == {"id": str(TARGET_ID)}

    async def test_errore_rpc_mappato(self):
        primary = FakePrimary()
        primary.rpc_errors["fn_promote_progettista"] = APIError(
            {"message": "Utente non trovato", "code": "P0001",
             "details": "user_not_found", "hint": None}
        )
        with pytest.raises(NotFoundError):
            await user_service.admin_update_user(
                primary, TARGET_ID, AdminUserUpdate(role="progettista"), ADMIN_ID
            )


class TestDemozioni:
    async def test_demozione_aggiorna_profiles_con_audit(self):
        primary = FakePrimary()
        await user_service.admin_update_user(
            primary, TARGET_ID, AdminUserUpdate(role="cliente"), ADMIN_ID
        )
        assert primary.rpc_calls == []
        [(_, _, payload, _)] = ops_for(primary, "profiles", "update")
        assert payload == {"role": "cliente"}
        [(_, _, audit, _)] = ops_for(primary, "audit_log", "insert")
        assert audit["action"] == "admin.role_changed"
        assert audit["payload"] == {"role": "cliente"}
        assert audit["target_user_id"] == str(TARGET_ID)

    async def test_audit_guasto_non_blocca(self):
        class BrokenAuditPrimary(FakePrimary):
            def table(self, name):
                query = super().table(name)
                if name == "audit_log":
                    async def boom():
                        raise RuntimeError("audit KO")

                    query.execute = boom
                return query

        primary = BrokenAuditPrimary()
        out = await user_service.admin_update_user(
            primary, TARGET_ID, AdminUserUpdate(role="admin"), ADMIN_ID
        )
        assert out.profile.email == "target@test.it"

    async def test_solo_is_active_niente_audit_ruolo(self):
        primary = FakePrimary()
        await user_service.admin_update_user(
            primary, TARGET_ID, AdminUserUpdate(is_active=False), ADMIN_ID
        )
        assert ops_for(primary, "audit_log", "insert") == []

    async def test_utente_inesistente(self):
        primary = FakePrimary(updates={"profiles": []})
        with pytest.raises(NotFoundError):
            await user_service.admin_update_user(
                primary, TARGET_ID, AdminUserUpdate(role="cliente"), ADMIN_ID
            )


class TestRagioneSociale:
    """L'azienda mostrata nell'elenco admin (`_ragione_sociale`): dossier più
    vecchio NON cancellato dell'embed company_profiles."""

    def test_vuoto_o_none(self):
        assert user_service._ragione_sociale(None) is None
        assert user_service._ragione_sociale([]) is None

    def test_piu_vecchia_non_cancellata(self):
        embed = [
            {"ragione_sociale": "Nuova", "deleted_at": None, "created_at": "2026-02-01"},
            {"ragione_sociale": "Vecchia", "deleted_at": None, "created_at": "2026-01-01"},
        ]
        assert user_service._ragione_sociale(embed) == "Vecchia"

    def test_cancellate_ignorate(self):
        embed = [{"ragione_sociale": "Del", "deleted_at": "2026-01-01", "created_at": "2026-01-01"}]
        assert user_service._ragione_sociale(embed) is None

    def test_embed_1a1_come_dict(self):
        embed = {"ragione_sociale": "ACME", "deleted_at": None, "created_at": "x"}
        assert user_service._ragione_sociale(embed) == "ACME"


class TestAutoLockout:
    async def test_verso_cliente_bloccato(self):
        primary = FakePrimary()
        with pytest.raises(BadRequestError):
            await user_service.admin_update_user(
                primary, TARGET_ID, AdminUserUpdate(role="cliente"), str(TARGET_ID)
            )
        assert primary.ops == [] and primary.rpc_calls == []

    async def test_verso_progettista_bloccato(self):
        """Anche progettista è un auto-lockout: il Literal esteso non deve
        aprire una scappatoia nella guardia."""
        primary = FakePrimary()
        with pytest.raises(BadRequestError):
            await user_service.admin_update_user(
                primary, TARGET_ID, AdminUserUpdate(role="progettista"), str(TARGET_ID)
            )
        assert primary.rpc_calls == []

    async def test_su_se_stesso_admin_resta_consentito(self):
        primary = FakePrimary()
        await user_service.admin_update_user(
            primary, TARGET_ID, AdminUserUpdate(role="admin"), str(TARGET_ID)
        )
        [(_, _, payload, _)] = ops_for(primary, "profiles", "update")
        assert payload == {"role": "admin"}

    async def test_autosospensione_bloccata(self):
        primary = FakePrimary()
        with pytest.raises(BadRequestError):
            await user_service.admin_update_user(
                primary, TARGET_ID, AdminUserUpdate(is_active=False), str(TARGET_ID)
            )
