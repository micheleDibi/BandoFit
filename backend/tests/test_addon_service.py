"""Test del catalogo add-on (gemello di plan_service, che finora non aveva
test diretti): filtro attivi, creazione con slug unico, aggiornamento."""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from postgrest.exceptions import APIError

from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.schemas.addon import AddonCreate, AddonUpdate
from app.services import addon_service

ADDON_ROW = {
    "id": 1,
    "nome": "Pacchetto AI",
    "slug": "pacchetto-ai",
    "descrizione": "10 AI-check extra",
    "prezzo": "49.00",
    "tipo_prezzo": "importo",
    "etichetta_prezzo": None,
    "ordering": 1,
    "is_active": True,
    "updated_at": "2026-07-07T10:00:00+00:00",
}


class FakeQuery:
    def __init__(self, owner, table: str):
        self._owner = owner
        self._table = table
        self._op = "select"
        self._payload = None
        self.filters: dict = {}

    def select(self, *args, **kwargs):
        return self

    def insert(self, payload):
        self._op, self._payload = "insert", payload
        return self

    def update(self, payload):
        self._op, self._payload = "update", payload
        return self

    def eq(self, column, value):
        self.filters[column] = value
        return self

    def order(self, column, **kwargs):
        self.filters["__order"] = column
        return self

    async def execute(self):
        self._owner.ops.append((self._table, self._op, self._payload, dict(self.filters)))
        if self._op == "select":
            return SimpleNamespace(data=self._owner.selects.get(self._table, []))
        if self._op == "insert":
            if self._owner.insert_fail_unique:
                raise APIError({
                    "message": "duplicate key value violates unique constraint",
                    "code": "23505", "hint": None, "details": None,
                })
            return SimpleNamespace(data=[{**ADDON_ROW, **(self._payload or {})}])
        if self._op == "update":
            if self._owner.update_returns_empty:
                return SimpleNamespace(data=[])
            return SimpleNamespace(data=[{**ADDON_ROW, **(self._payload or {})}])
        return SimpleNamespace(data=[])


class FakePrimary:
    def __init__(self, selects: dict | None = None):
        self.selects = selects or {}
        self.ops: list = []
        self.rpcs: list = []
        self.rpc_result: dict = {}
        self.insert_fail_unique = False
        self.update_returns_empty = False

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self, name)

    def rpc(self, fn: str, params: dict):
        self.rpcs.append((fn, params))
        owner = self

        class _Rpc:
            async def execute(self_inner):
                return SimpleNamespace(data=owner.rpc_result.get(fn))

        return _Rpc()

    def ops_for(self, table: str, op: str) -> list:
        return [(payload, filters) for t, o, payload, filters in self.ops if t == table and o == op]


class TestList:
    async def test_active_filtra_e_ordina(self):
        primary = FakePrimary({"addons": [ADDON_ROW]})
        out = await addon_service.list_active_addons(primary)
        [(_, filters)] = primary.ops_for("addons", "select")
        assert filters["is_active"] is True
        assert filters["__order"] == "ordering"
        assert out[0].slug == "pacchetto-ai"
        assert out[0].prezzo == Decimal("49.00")

    async def test_all_non_filtra(self):
        primary = FakePrimary({"addons": [ADDON_ROW]})
        await addon_service.list_all_addons(primary)
        [(_, filters)] = primary.ops_for("addons", "select")
        assert "is_active" not in filters
        assert filters["__order"] == "ordering"


class TestAcquistabilita:
    """Acquistabilità per l'utente corrente (0030): collegato attivo →
    solo_titolare su tutti i paid; allocativo con base non idonea →
    piano_non_idoneo. Il gate vero resta nel checkout."""

    USER = {"id": "aaaaaaaa-0000-0000-0000-000000000001"}
    ALLOCATIVO = {**ADDON_ROW, "id": 2, "slug": "azienda-aggiuntiva",
                  "risorsa": "companies"}

    @pytest.fixture
    def membership(self, monkeypatch):
        holder = {"value": None}

        async def get_membership(primary, user_id):
            return holder["value"]

        monkeypatch.setattr("app.services.family_service.get_membership", get_membership)
        return holder

    async def test_senza_user_nessun_calcolo(self):
        primary = FakePrimary({"addons": [dict(self.ALLOCATIVO)]})
        out = await addon_service.list_active_addons(primary)
        assert out[0].acquistabile is True and primary.rpcs == []

    async def test_collegato_attivo_solo_titolare(self, membership):
        membership["value"] = {"status": "active", "parent_id": "p"}
        primary = FakePrimary({"addons": [ADDON_ROW, dict(self.ALLOCATIVO)]})
        out = await addon_service.list_active_addons(primary, self.USER)
        assert all(not a.acquistabile for a in out)
        assert {a.motivo_non_acquistabile for a in out} == {"solo_titolare"}
        assert primary.rpcs == []  # niente snapshot: il motivo è già deciso

    async def test_allocativo_base_non_idonea(self, membership):
        primary = FakePrimary({"addons": [ADDON_ROW, dict(self.ALLOCATIVO)]})
        primary.rpc_result["fn_entitlement_snapshot"] = {
            "companies": {"base": 1, "extra": 0, "effettivo": 1, "usato": 1, "residuo": 0}}
        out = await addon_service.list_active_addons(primary, self.USER)
        normale, allocativo = out
        assert normale.acquistabile is True and normale.motivo_non_acquistabile is None
        assert allocativo.acquistabile is False
        assert allocativo.motivo_non_acquistabile == "piano_non_idoneo"

    async def test_allocativo_base_idonea(self, membership):
        primary = FakePrimary({"addons": [dict(self.ALLOCATIVO)]})
        primary.rpc_result["fn_entitlement_snapshot"] = {
            "companies": {"base": 10, "extra": 0, "effettivo": 10, "usato": 2, "residuo": 8}}
        out = await addon_service.list_active_addons(primary, self.USER)
        assert out[0].acquistabile is True

    async def test_gratis_non_toccato(self, membership):
        membership["value"] = {"status": "active", "parent_id": "p"}
        gratis = {**ADDON_ROW, "id": 3, "slug": "omaggio", "tipo_prezzo": "gratis"}
        primary = FakePrimary({"addons": [gratis]})
        out = await addon_service.list_active_addons(primary, self.USER)
        assert out[0].acquistabile is True  # la CTA «Attiva» non passa dal checkout


class TestCreate:
    async def test_inserisce_il_payload(self):
        primary = FakePrimary()
        data = AddonCreate(nome="Alert Plus", slug="alert-plus", prezzo=Decimal("19.90"))
        out = await addon_service.create_addon(primary, data)
        [(inserted, _)] = primary.ops_for("addons", "insert")
        assert inserted["slug"] == "alert-plus"
        assert inserted["prezzo"] == "19.90"  # model_dump json-mode preserva i decimali
        assert inserted["is_active"] is True
        assert out.nome == "Alert Plus"

    async def test_slug_duplicato_409(self):
        primary = FakePrimary()
        primary.insert_fail_unique = True
        with pytest.raises(ConflictError):
            await addon_service.create_addon(
                primary, AddonCreate(nome="X", slug="pacchetto-ai", prezzo=Decimal("1"))
            )

    def test_slug_malformato_respinto(self):
        with pytest.raises(ValueError):
            AddonCreate(nome="X", slug="Slug Con Spazi", prezzo=Decimal("1"))
        with pytest.raises(ValueError):
            AddonCreate(nome="X", slug="maiuscole-No", prezzo=Decimal("1"))

    async def test_su_richiesta_con_etichetta(self):
        primary = FakePrimary()
        data = AddonCreate(
            nome="Consulenza dedicata",
            slug="consulenza",
            prezzo=Decimal("0"),
            tipo_prezzo="su_richiesta",
            etichetta_prezzo="Parliamone insieme",
        )
        await addon_service.create_addon(primary, data)
        [(inserted, _)] = primary.ops_for("addons", "insert")
        assert inserted["tipo_prezzo"] == "su_richiesta"
        assert inserted["etichetta_prezzo"] == "Parliamone insieme"

    def test_tipo_prezzo_non_valido_respinto(self):
        with pytest.raises(ValueError):
            AddonCreate(nome="X", slug="x", prezzo=Decimal("1"), tipo_prezzo="sconto")


class TestUpdate:
    async def test_exclude_unset(self):
        primary = FakePrimary()
        out = await addon_service.update_addon(primary, 1, AddonUpdate(prezzo=Decimal("59.00")))
        [(changes, filters)] = primary.ops_for("addons", "update")
        assert changes == {"prezzo": "59.00"}  # SOLO il campo passato
        assert filters["id"] == 1
        assert out.id == 1

    async def test_exclude_unset_solo_tipo_prezzo(self):
        primary = FakePrimary()
        await addon_service.update_addon(primary, 1, AddonUpdate(tipo_prezzo="gratis"))
        [(changes, _)] = primary.ops_for("addons", "update")
        assert changes == {"tipo_prezzo": "gratis"}

    async def test_vuoto_400(self):
        with pytest.raises(BadRequestError):
            await addon_service.update_addon(FakePrimary(), 1, AddonUpdate())

    async def test_non_trovato_404(self):
        primary = FakePrimary()
        primary.update_returns_empty = True
        with pytest.raises(NotFoundError):
            await addon_service.update_addon(primary, 999, AddonUpdate(is_active=False))
