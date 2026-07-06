"""Test del flusso di import IT-full: cooldown, lock, registro consumi,
permessi famiglia e persistenza — con client PostgREST e openapi finti."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.errors import (
    AppError,
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    OpenapiNotConfiguredError,
    OpenapiTimeoutError,
    OpenapiUpstreamError,
)
from app.clients.openapi import OpenapiInvalidIdError
from app.services import openapi_service

FIXTURES = Path(__file__).parent / "fixtures" / "openapi"
USER = {"id": "a0000000-0000-0000-0000-000000000001", "role": "cliente", "is_active": True}
PIVA = "14061981008"


def it_full_payload() -> dict:
    return json.loads((FIXTURES / "it_full_sample.json").read_text())["data"]


# ------------------------------------------------------------------- finti

class FakeQuery:
    def __init__(self, primary, table: str):
        self._primary = primary
        self._table = table
        self._op = "select"
        self._payload = None

    def __getattr__(self, name):
        def method(*args, **kwargs):
            if name in ("insert", "update", "upsert", "delete"):
                self._op = name
                self._payload = args[0] if args else None
            return self

        return method

    async def execute(self):
        self._primary.ops.append((self._table, self._op, self._payload))
        if self._op == "select":
            return SimpleNamespace(data=self._primary.selects.get(self._table, []))
        return SimpleNamespace(data=[])


class FakePrimary:
    """Registra le operazioni; `selects` configura le risposte alle SELECT."""

    def __init__(self, selects: dict | None = None, lock: bool = True):
        self.selects = selects or {}
        self.lock = lock
        self.ops: list = []
        self.rpcs: list = []

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self, name)

    def rpc(self, name: str, params: dict):
        self.rpcs.append((name, params))
        primary = self

        class _Rpc:
            async def execute(self_inner):
                return SimpleNamespace(
                    data=primary.lock if name == "fn_acquire_import_lock" else None
                )

        return _Rpc()

    # helper d'ispezione
    def ops_for(self, table: str, op: str) -> list:
        return [payload for t, o, payload in self.ops if t == table and o == op]


def fake_openapi(result=None, error: Exception | None = None, enabled=True, sandbox=False):
    async def it_full(piva):
        if error:
            raise error
        return result

    return SimpleNamespace(enabled=enabled, sandbox=sandbox, it_full=it_full)


@pytest.fixture(autouse=True)
def stub_settings(monkeypatch):
    for key, value in {
        "PRIMARY_SUPABASE_URL": "https://dummy.supabase.co",
        "PRIMARY_SUPABASE_SERVICE_ROLE_KEY": "k",
        "SECONDARY_SUPABASE_URL": "https://d2.supabase.co",
        "SECONDARY_SUPABASE_ANON_KEY": "k",
        "COMPANY_IMPORT_COOLDOWN_MINUTES": "10",
    }.items():
        monkeypatch.setenv(key, value)
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def no_membership(monkeypatch):
    async def membership(primary, user_id):
        return None

    monkeypatch.setattr(
        "app.services.family_service.get_membership", membership
    )


@pytest.fixture(autouse=True)
def fake_lookups(monkeypatch):
    lookups = SimpleNamespace(
        codici_ateco=[SimpleNamespace(id=850, codice="85", descrizione="Istruzione")],
        regioni=[SimpleNamespace(id=12, nome="Lazio")],
        beneficiari=[SimpleNamespace(id=1, nome="Micro-imprese"), SimpleNamespace(id=2, nome="PMI")],
        settori=[], tipologie=[], modalita=[], programmi=[],
    )

    async def get_lookups(secondary):
        return lookups

    monkeypatch.setattr("app.services.lookup_service.get_lookups", get_lookups)


@pytest.fixture(autouse=True)
def fake_company_response(monkeypatch):
    async def get_company(primary, user):
        from app.schemas.company import CompanyResponse

        return CompanyResponse(editable=True, company=None)

    monkeypatch.setattr("app.services.company_service.get_company", get_company)


COMPANY_ROW = {
    "id": "c0000000-0000-0000-0000-000000000001",
    "ragione_sociale": "ACME", "partita_iva": PIVA, "codice_fiscale": None,
    "forma_giuridica": None, "ateco_id": None, "ateco_codice": None,
    "ateco_descrizione": None, "settore_id": None, "settore_nome": None,
    "regione_id": None, "regione_nome": None, "anno_fondazione": None,
    "indirizzo": None, "comune": None, "provincia": None, "cap": None,
    "classe_dimensionale": None, "numero_dipendenti": None,
    "fascia_fatturato": None, "pec": None, "telefono": None, "sito_web": None,
}


class TestGuardieIniziali:
    async def test_non_configurato(self):
        with pytest.raises(OpenapiNotConfiguredError):
            await openapi_service.import_company(
                FakePrimary(), None, fake_openapi(enabled=False), USER, PIVA
            )

    async def test_figlio_attivo_bloccato(self, monkeypatch):
        async def membership(primary, user_id):
            return {"status": "active", "parent_id": "x"}

        monkeypatch.setattr("app.services.family_service.get_membership", membership)
        with pytest.raises(ForbiddenError):
            await openapi_service.import_company(
                FakePrimary(), None, fake_openapi(), USER, PIVA
            )

    async def test_piva_mancante_e_invalida(self):
        with pytest.raises(BadRequestError):
            await openapi_service.import_company(
                FakePrimary(), None, fake_openapi(), USER, None
            )
        with pytest.raises(BadRequestError):
            await openapi_service.import_company(
                FakePrimary(), None, fake_openapi(), USER, "14061981009"
            )


class TestCooldownELock:
    async def test_cooldown_recente_blocca(self):
        primary = FakePrimary(
            selects={
                "company_profiles": [COMPANY_ROW],
                "company_data": [
                    {"fetched_at": datetime.now(timezone.utc).isoformat(), "fetch_count": 1}
                ],
            }
        )
        with pytest.raises(AppError) as exc:
            await openapi_service.import_company(primary, None, fake_openapi(), USER, PIVA)
        assert exc.value.code == "import_cooldown"
        assert primary.rpcs == []  # nessun lock nemmeno tentato

    async def test_cooldown_scaduto_procede(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        primary = FakePrimary(
            selects={
                "company_profiles": [COMPANY_ROW],
                "company_data": [{"fetched_at": old, "fetch_count": 3}],
            }
        )
        result = await openapi_service.import_company(
            primary, None, fake_openapi(result=it_full_payload()), USER, PIVA
        )
        upserts = primary.ops_for("company_data", "upsert")
        assert upserts[0]["fetch_count"] == 4

    async def test_lock_occupato(self):
        primary = FakePrimary(selects={"company_profiles": [COMPANY_ROW]}, lock=False)
        called = []

        async def it_full(piva):
            called.append(piva)

        openapi = SimpleNamespace(enabled=True, sandbox=False, it_full=it_full)
        with pytest.raises(AppError) as exc:
            await openapi_service.import_company(primary, None, openapi, USER, PIVA)
        assert exc.value.code == "import_in_progress"
        assert called == []  # la chiamata a pagamento non parte


class TestEsitiChiamata:
    async def test_piva_non_trovata(self):
        primary = FakePrimary(selects={"company_profiles": [COMPANY_ROW]})
        with pytest.raises(NotFoundError):
            await openapi_service.import_company(
                primary, None, fake_openapi(error=OpenapiInvalidIdError()), USER, PIVA
            )
        events = primary.ops_for("api_usage_events", "insert")
        assert events[0]["outcome"] == "error"
        assert ("fn_release_import_lock", {"p_parent_id": USER["id"]}) in primary.rpcs

    async def test_timeout_ledger_e_lock_non_rilasciato(self):
        primary = FakePrimary(selects={"company_profiles": [COMPANY_ROW]})
        with pytest.raises(OpenapiTimeoutError):
            await openapi_service.import_company(
                primary, None, fake_openapi(error=OpenapiTimeoutError()), USER, PIVA
            )
        events = primary.ops_for("api_usage_events", "insert")
        assert events[0]["outcome"] == "timeout_unknown"
        assert events[0]["cost_cents"] == 30  # possibile addebito
        released = [name for name, _ in primary.rpcs if name == "fn_release_import_lock"]
        assert released == []  # scade da solo: protegge dal doppio addebito

    async def test_mismatch_piva_non_persiste(self):
        data = it_full_payload()
        data["companyDetails"]["vatCode"] = "00000000000"
        data["companyDetails"]["taxCode"] = "00000000000"
        primary = FakePrimary(selects={"company_profiles": [COMPANY_ROW]})
        with pytest.raises(OpenapiUpstreamError):
            await openapi_service.import_company(
                primary, None, fake_openapi(result=data), USER, PIVA
            )
        assert primary.ops_for("company_data", "upsert") == []
        events = primary.ops_for("api_usage_events", "insert")
        assert events[0]["request_meta"]["mismatch"] is True
        assert ("fn_release_import_lock", {"p_parent_id": USER["id"]}) in primary.rpcs


class TestImportRiuscito:
    async def test_flusso_completo(self):
        primary = FakePrimary(selects={"company_profiles": [COMPANY_ROW]})
        result = await openapi_service.import_company(
            primary, None, fake_openapi(result=it_full_payload()), USER, PIVA
        )
        # ledger success con costo pieno
        events = primary.ops_for("api_usage_events", "insert")
        assert events[0]["outcome"] == "success" and events[0]["cost_cents"] == 30
        # autofill: solo update dei campi vuoti (ragione_sociale utente intatta)
        updates = primary.ops_for("company_profiles", "update")[0]
        assert "ragione_sociale" not in updates
        assert updates["ateco_id"] == 850 and updates["regione_id"] == 12
        # dati certificati + persone + audit
        upsert = primary.ops_for("company_data", "upsert")[0]
        assert upsert["piva_fetched"] == PIVA and upsert["fetch_count"] == 1
        assert upsert["stato_impresa"] == "Attiva"
        assert primary.ops_for("company_people", "delete")
        people_insert = primary.ops_for("company_people", "insert")[0]
        assert people_insert[0]["kind"] == "manager"
        assert primary.ops_for("audit_log", "insert")[0]["action"] == "company.imported"
        # lock rilasciato
        assert ("fn_release_import_lock", {"p_parent_id": USER["id"]}) in primary.rpcs
        # risultato
        assert result.sandbox is False
        assert result.dossier["anagrafica"]["stato"] == "Attiva"
        assert result.autofill.conflicts == [
            {"campo": "ragione_sociale", "valore_attuale": "ACME",
             "valore_certificato": it_full_payload()["companyDetails"]["companyName"]}
        ]
        assert result.people[0].is_legale_rappresentante is True

    async def test_primo_import_crea_il_profilo_aziendale(self):
        primary = FakePrimary(selects={"company_profiles": []})

        # dopo l'insert la select deve trovare la riga
        original_execute = FakeQuery.execute

        async def execute(self):
            if self._table == "company_profiles" and self._op == "insert":
                self._primary.selects["company_profiles"] = [dict(COMPANY_ROW, ragione_sociale=None)]
            return await original_execute(self)

        FakeQuery.execute = execute
        try:
            await openapi_service.import_company(
                primary, None, fake_openapi(result=it_full_payload()), USER, PIVA
            )
        finally:
            FakeQuery.execute = original_execute

        created = primary.ops_for("company_profiles", "insert")[0]
        assert created["partita_iva"] == PIVA
        assert created["ragione_sociale"].startswith("ENTE RICERCA")

    async def test_sandbox_costo_zero(self):
        primary = FakePrimary(selects={"company_profiles": [COMPANY_ROW]})
        await openapi_service.import_company(
            primary, None, fake_openapi(result=it_full_payload(), sandbox=True), USER, PIVA
        )
        events = primary.ops_for("api_usage_events", "insert")
        assert events[0]["cost_cents"] == 0
        assert primary.ops_for("company_data", "upsert")[0]["sandbox"] is True


class TestDossier:
    async def test_mai_importato(self):
        primary = FakePrimary(selects={"company_profiles": [COMPANY_ROW]})
        resp = await openapi_service.get_dossier(primary, USER)
        assert resp.imported is False and resp.editable is True

    async def test_dossier_del_titolare(self):
        primary = FakePrimary(
            selects={
                "company_profiles": [COMPANY_ROW],
                "company_data": [
                    {
                        "raw": it_full_payload(), "derived": {"classe_dimensionale": "micro"},
                        "piva_fetched": PIVA, "sandbox": False, "fetch_count": 1,
                        "fetched_at": "2026-07-06T10:00:00+00:00",
                    }
                ],
                "company_people": [
                    {
                        "kind": "manager", "nome": "MICHELE", "cognome": "X",
                        "denominazione": None, "codice_fiscale": None,
                        "data_nascita": "1985-06-04", "luogo_nascita": None,
                        "genere": "M", "ruoli": [], "is_legale_rappresentante": True,
                        "quota_percentuale": None, "data_inizio_carica": None,
                    }
                ],
            }
        )
        resp = await openapi_service.get_dossier(primary, USER)
        assert resp.imported is True and resp.editable is True
        assert resp.dossier["anagrafica"]["denominazione"].startswith("ENTE")
        assert resp.people[0].nome == "MICHELE"
        assert resp.derived["classe_dimensionale"] == "micro"

    async def test_figlio_attivo_legge_la_famiglia(self, monkeypatch):
        async def membership(primary, user_id):
            return {"status": "active", "parent_id": "p0000000-0000-0000-0000-000000000001"}

        monkeypatch.setattr("app.services.family_service.get_membership", membership)
        primary = FakePrimary(selects={"company_profiles": []})
        resp = await openapi_service.get_dossier(primary, USER)
        assert resp.editable is False and resp.imported is False
