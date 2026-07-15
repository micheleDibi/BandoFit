"""Gestione multi-azienda (fase 2): scrittura per `id` dell'azienda attiva,
elenco/creazione/soft-delete con limite di piano, mappatura degli errori RPC."""

from types import SimpleNamespace

import pytest
from postgrest.exceptions import APIError

from app.api.deps import ActiveCompany
from app.core.errors import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.schemas.bando import LookupsOut
from app.schemas.company import CompanyCreate, CompanyIn
from app.services import company_service

OWNER = "a0000000-0000-0000-0000-000000000001"
COMPANY = "c0000000-0000-0000-0000-000000000001"
COMPANY2 = "c0000000-0000-0000-0000-000000000002"
NEW = "c0000000-0000-0000-0000-0000000000aa"

LOOKUPS = LookupsOut(
    regioni=[{"id": 10, "nome": "Lombardia"}],
    settori=[], beneficiari=[],
    codici_ateco=[{"id": 3, "codice": "49", "descrizione": "Trasporto"}],
    tipologie_bando=[], modalita_erogazione=[], programmi=[],
)


def _active(company_id=COMPANY, editable=True):
    return ActiveCompany(company_id=company_id, owner_id=OWNER, editable=editable)


class FakeQuery:
    def __init__(self, primary, table):
        self.primary = primary
        self.table = table
        self.op = "select"
        self.payload = None

    def _passthrough(self, *a, **k):
        return self

    select = eq = is_ = order = limit = _passthrough

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    async def execute(self):
        self.primary.ops.append((self.table, self.op, self.payload))
        if self.op == "select":
            return SimpleNamespace(data=self.primary.selects.get(self.table, []))
        if self.op == "insert":
            row = {"id": NEW, "created_at": "2026-07-15T00:00:00+00:00"}
            if isinstance(self.payload, dict):
                row.update(self.payload)
            return SimpleNamespace(data=[row])
        return SimpleNamespace(data=[])


class FakePrimary:
    def __init__(self, selects=None, rpc_data=None, rpc_error=None):
        self.selects = selects or {}
        self.rpc_data = rpc_data or {}
        self.rpc_error = rpc_error or {}
        self.ops: list = []
        self.rpcs: list = []

    def table(self, name):
        return FakeQuery(self, name)

    def rpc(self, name, params):
        self.rpcs.append((name, params))
        primary = self

        class _Rpc:
            async def execute(self_inner):
                if name in primary.rpc_error:
                    raise primary.rpc_error[name]
                return SimpleNamespace(data=primary.rpc_data.get(name))

        return _Rpc()

    def ops_for(self, table, op):
        return [p for t, o, p in self.ops if t == table and o == op]


def _api_error(detail: str) -> APIError:
    return APIError({"message": "boom", "code": "P0001", "hint": None, "details": detail})


@pytest.fixture(autouse=True)
def fake_lookups(monkeypatch):
    async def get_lookups(secondary):
        return LOOKUPS

    monkeypatch.setattr("app.services.lookup_service.get_lookups", get_lookups)


@pytest.fixture(autouse=True)
def spy_invalidate(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        "app.services.compatibility.invalidate_company_facets", calls.append
    )
    return calls


class TestUpsertCompany:
    async def test_figlio_attivo_bloccato(self):
        with pytest.raises(ForbiddenError):
            await company_service.upsert_company(
                FakePrimary(), None, _active(editable=False), CompanyIn(
                    ragione_sociale="ACME", partita_iva="01234567890"
                )
            )

    async def test_azienda_esistente_update_per_id(self, spy_invalidate):
        primary = FakePrimary(selects={"company_profiles": [
            {"ragione_sociale": "ACME", "partita_iva": "01234567890", "beneficiari": []}
        ]})
        await company_service.upsert_company(
            primary, None, _active(), CompanyIn(ragione_sociale="ACME", partita_iva="01234567890")
        )
        # scrittura per id, MAI un insert o un parent_id nel payload
        update = primary.ops_for("company_profiles", "update")[0]
        assert "parent_id" not in update
        assert primary.ops_for("company_profiles", "insert") == []
        # cache invalidata sull'id dell'azienda attiva
        assert spy_invalidate == [COMPANY]

    async def test_bootstrap_senza_azienda_fa_insert(self, spy_invalidate):
        primary = FakePrimary()
        await company_service.upsert_company(
            primary, None, _active(company_id=None),
            CompanyIn(ragione_sociale="Nuova", partita_iva="01234567890"),
        )
        insert = primary.ops_for("company_profiles", "insert")[0]
        assert insert["parent_id"] == OWNER
        assert insert["ragione_sociale"] == "Nuova"
        # l'id invalidato è quello della riga appena creata, non l'owner
        assert spy_invalidate == [NEW]


class TestListCompanies:
    async def test_elenco_con_limite_e_attiva(self):
        primary = FakePrimary(
            selects={
                "company_profiles": [
                    {"id": COMPANY, "ragione_sociale": "A", "partita_iva": "01234567890",
                     "created_at": "2026-01-01T00:00:00+00:00"},
                    {"id": COMPANY2, "ragione_sociale": "B", "partita_iva": "01234567891",
                     "created_at": "2026-02-01T00:00:00+00:00"},
                ]
            },
            rpc_data={"fn_effective_max_aziende": 10},
        )
        out = await company_service.list_companies(primary, OWNER)
        assert out.max_aziende == 10 and out.usate == 2
        # la prima (più vecchia) è quella attiva di default
        assert out.aziende[0].attiva is True and out.aziende[1].attiva is False

    async def test_nessuna_azienda(self):
        primary = FakePrimary(rpc_data={"fn_effective_max_aziende": 1})
        out = await company_service.list_companies(primary, OWNER)
        assert out.aziende == [] and out.usate == 0 and out.max_aziende == 1


class TestCreateCompany:
    async def test_creazione_ok(self):
        primary = FakePrimary(
            selects={"company_profiles": [
                {"id": NEW, "ragione_sociale": "ACME",
                 "partita_iva": "01234567890", "created_at": "2026-07-15T00:00:00+00:00"}
            ]},
            rpc_data={"fn_create_company": NEW},
        )
        summary = await company_service.create_company(
            primary, OWNER, CompanyCreate(ragione_sociale="ACME", partita_iva="01234567890")
        )
        assert summary.ragione_sociale == "ACME"
        assert primary.rpcs[0][0] == "fn_create_company"

    @pytest.mark.parametrize(
        "detail,error_cls",
        [
            ("company_limit_reached", ConflictError),
            ("partita_iva_invalid", BadRequestError),
            ("ragione_sociale_required", BadRequestError),
            ("owner_not_found", NotFoundError),
        ],
    )
    async def test_errori_rpc_mappati(self, detail, error_cls):
        primary = FakePrimary(rpc_error={"fn_create_company": _api_error(detail)})
        with pytest.raises(error_cls):
            await company_service.create_company(
                primary, OWNER, CompanyCreate(ragione_sociale="ACME", partita_iva="01234567890")
            )


class TestSoftDelete:
    async def test_soft_delete_ok_invalida_cache(self, spy_invalidate):
        primary = FakePrimary(rpc_data={"fn_soft_delete_company": None})
        await company_service.soft_delete_company(primary, OWNER, COMPANY)
        assert primary.rpcs[0][0] == "fn_soft_delete_company"
        assert spy_invalidate == [COMPANY]

    async def test_azienda_inesistente_404(self):
        primary = FakePrimary(rpc_error={"fn_soft_delete_company": _api_error("company_not_found")})
        with pytest.raises(NotFoundError):
            await company_service.soft_delete_company(primary, OWNER, COMPANY)


class TestEffectiveMax:
    async def test_ritorna_intero(self):
        primary = FakePrimary(rpc_data={"fn_effective_max_aziende": 7})
        assert await company_service.effective_max_aziende(primary, OWNER) == 7

    async def test_none_degrada_a_uno(self):
        primary = FakePrimary(rpc_data={"fn_effective_max_aziende": None})
        assert await company_service.effective_max_aziende(primary, OWNER) == 1
