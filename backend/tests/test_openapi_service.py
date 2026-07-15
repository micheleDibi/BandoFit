"""Test del flusso di import IT-full a due fasi — anteprima (a pagamento) e
conferma (gratuita) — con client PostgREST e openapi finti.

Il tema di fondo è il denaro: ogni chiamata IT-full costa credito reale.
I test presidiano i tre punti in cui si può pagare due volte (cooldown, lock,
riuso del draft) e l'unico in cui si può pagare per nulla (il draft scaduto)."""

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
from app.api.deps import ActiveCompany
from app.clients.openapi import OpenapiInvalidIdError
from app.services import openapi_service

FIXTURES = Path(__file__).parent / "fixtures" / "openapi"
USER = {"id": "a0000000-0000-0000-0000-000000000001", "role": "cliente", "is_active": True}
PIVA = "14061981008"


def _active(company_id: str | None = "c-openapi", editable: bool = True) -> ActiveCompany:
    return ActiveCompany(company_id=company_id, owner_id=USER["id"], editable=editable)


ALTRA_PIVA = "00000000000"  # checksum valido, azienda diversa


def it_full_payload() -> dict:
    return json.loads((FIXTURES / "it_full_sample.json").read_text())["data"]


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def draft_row(
    piva: str = PIVA, *, payload: dict | None = None, sandbox: bool = False,
    eta_minuti: int = 0, ttl_minuti: int = 30,
) -> dict:
    """Riga di `company_import_drafts`. `eta_minuti` > `ttl_minuti` = draft scaduto."""
    fetched_at = datetime.now(timezone.utc) - timedelta(minutes=eta_minuti)
    return {
        "partita_iva": piva,
        "raw": it_full_payload() if payload is None else payload,
        "sandbox": sandbox,
        "fetched_at": fetched_at.isoformat(),
        "expires_at": (fetched_at + timedelta(minutes=ttl_minuti)).isoformat(),
    }


# ------------------------------------------------------------------- finti

class FakeQuery:
    def __init__(self, primary, table: str):
        self._primary = primary
        self._table = table
        self._op = "select"
        self._payload = None
        self._filters: list[tuple[str, str]] = []

    def __getattr__(self, name):
        def method(*args, **kwargs):
            if name in ("insert", "update", "upsert", "delete"):
                self._op = name
                self._payload = args[0] if args else None
            elif name == "gt" and len(args) == 2:
                # L'unico filtro applicato davvero: le SELECT sui draft si
                # reggono su `expires_at > now()`. Simulare anche gli `eq`
                # richiederebbe righe complete di chiavi esterne in ogni fixture.
                self._filters.append((args[0], args[1]))
            return self

        return method

    def _matches(self, row: dict) -> bool:
        for column, value in self._filters:
            if column not in row:
                continue
            if _parse_iso(row[column]) <= _parse_iso(value):
                return False
        return True

    async def execute(self):
        self._primary.ops.append((self._table, self._op, self._payload))
        if self._op == "select":
            rows = self._primary.selects.get(self._table, [])
            return SimpleNamespace(data=[row for row in rows if self._matches(row)])
        if self._op == "insert" and isinstance(self._payload, dict):
            # come PostgREST: l'insert ritorna la riga (id/created_at generati).
            # created_at DINAMICO: una data fissa farebbe scattare i failsafe
            # "stale" dei servizi col passare del tempo reale.
            return SimpleNamespace(
                data=[
                    {
                        "id": f"gen-{self._table}-{len(self._primary.ops)}",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        **self._payload,
                    }
                ]
            )
        return SimpleNamespace(data=[])


class FakeStorage:
    """Storage Supabase finto: registra bucket creati, upload e download."""

    def __init__(self):
        self.buckets: list[str] = []
        self.uploads: list[tuple[str, bytes]] = []
        self.files: dict[str, bytes] = {}

    async def create_bucket(self, bucket_id, name=None, options=None):
        self.buckets.append(bucket_id)

    def from_(self, bucket):
        return self

    async def upload(self, path, file, file_options=None):
        self.uploads.append((path, file))
        self.files[path] = file

    async def download(self, path):
        return self.files[path]


class FakePrimary:
    """Registra le operazioni; `selects` configura le risposte alle SELECT."""

    def __init__(self, selects: dict | None = None, lock: bool = True):
        self.selects = selects or {}
        self.lock = lock
        self.ops: list = []
        self.rpcs: list = []
        self.storage = FakeStorage()

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
        "COMPANY_IMPORT_DRAFT_TTL_MINUTES": "30",
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
    async def _stub(*_args, **_kwargs):
        from app.schemas.company import CompanyResponse

        return CompanyResponse(editable=True, company=None)

    # L'import (`_persist_import`) legge i dati dell'azienda appena scritta per
    # `id`; il GET pubblico passa dal resolver dell'azienda attiva.
    monkeypatch.setattr("app.services.company_service.get_company", _stub)
    monkeypatch.setattr("app.services.company_service.company_response_for_owner", _stub)
    monkeypatch.setattr("app.services.company_service.company_response_for_id", _stub)


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
            await openapi_service.preview_import(
                FakePrimary(), None, fake_openapi(enabled=False), _active(), PIVA
            )

    async def test_figlio_attivo_bloccato(self):
        # Il resolver marca il figlio attivo come editable=False: il servizio
        # blocca sia l'anteprima sia la conferma (non è una scorciatoia).
        figlio = _active(editable=False)
        with pytest.raises(ForbiddenError):
            await openapi_service.preview_import(
                FakePrimary(), None, fake_openapi(), figlio, PIVA
            )
        primary = FakePrimary(selects={"company_import_drafts": [draft_row()]})
        with pytest.raises(ForbiddenError):
            await openapi_service.confirm_import(primary, None, figlio, PIVA)
        assert primary.ops_for("company_data", "upsert") == []

    async def test_piva_mancante_e_invalida(self):
        with pytest.raises(BadRequestError):
            await openapi_service.preview_import(
                FakePrimary(), None, fake_openapi(), _active(), None
            )
        with pytest.raises(BadRequestError):
            await openapi_service.preview_import(
                FakePrimary(), None, fake_openapi(), _active(), "14061981009"
            )


class TestCooldownELock:
    """Il cooldown protegge il FETCH (l'anteprima), non la scrittura."""

    async def test_cooldown_recente_blocca_lanteprima(self):
        primary = FakePrimary(
            selects={
                "company_profiles": [COMPANY_ROW],
                "company_data": [
                    {"fetched_at": datetime.now(timezone.utc).isoformat(), "fetch_count": 1}
                ],
            }
        )
        with pytest.raises(AppError) as exc:
            await openapi_service.preview_import(primary, None, fake_openapi(), _active(), PIVA)
        assert exc.value.code == "import_cooldown"
        assert primary.rpcs == []  # nessun lock nemmeno tentato

    async def test_cooldown_conta_anche_il_draft_di_unaltra_piva(self):
        """Senza questo, cambiare P.IVA a ogni tentativo drenerebbe il credito."""
        primary = FakePrimary(
            selects={
                "company_profiles": [COMPANY_ROW],
                "company_import_drafts": [draft_row(ALTRA_PIVA, eta_minuti=1)],
            }
        )
        with pytest.raises(AppError) as exc:
            await openapi_service.preview_import(primary, None, fake_openapi(), _active(), PIVA)
        assert exc.value.code == "import_cooldown"

    async def test_cooldown_scaduto_procede(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        primary = FakePrimary(
            selects={
                "company_profiles": [COMPANY_ROW],
                "company_data": [{"fetched_at": old, "fetch_count": 3}],
            }
        )
        await openapi_service.preview_import(
            primary, None, fake_openapi(result=it_full_payload()), _active(), PIVA
        )
        assert primary.ops_for("company_import_drafts", "upsert")

    async def test_lock_occupato(self):
        expires = (datetime.now(timezone.utc) + timedelta(minutes=4, seconds=30)).isoformat()
        primary = FakePrimary(
            selects={
                "company_profiles": [COMPANY_ROW],
                "company_import_locks": [{"expires_at": expires}],
            },
            lock=False,
        )
        called = []

        async def it_full(piva):
            called.append(piva)

        openapi = SimpleNamespace(enabled=True, sandbox=False, it_full=it_full)
        with pytest.raises(AppError) as exc:
            await openapi_service.preview_import(primary, None, openapi, _active(), PIVA)
        assert exc.value.code == "import_in_progress"
        assert called == []  # la chiamata a pagamento non parte
        # il messaggio dichiara l'attesa reale, non «qualche istante»
        assert "5 minuti" in exc.value.message

    async def test_conferma_non_valuta_il_cooldown(self):
        """I dati sono già pagati: rifiutare la conferma li butterebbe via."""
        primary = FakePrimary(
            selects={
                "company_profiles": [COMPANY_ROW],
                "company_data": [
                    {"fetched_at": datetime.now(timezone.utc).isoformat(), "fetch_count": 1}
                ],
                "company_import_drafts": [draft_row()],
            }
        )
        result = await openapi_service.confirm_import(primary, None, _active(), PIVA)
        assert result.sandbox is False
        assert primary.ops_for("company_data", "upsert")[0]["fetch_count"] == 2

    async def test_lock_occupato_in_conferma(self):
        primary = FakePrimary(
            selects={
                "company_profiles": [COMPANY_ROW],
                "company_import_drafts": [draft_row()],
            },
            lock=False,
        )
        with pytest.raises(AppError) as exc:
            await openapi_service.confirm_import(primary, None, _active(), PIVA)
        assert exc.value.code == "import_in_progress"
        assert primary.ops_for("company_data", "upsert") == []
        # TTL breve: la conferma non aspetta nessuna rete esterna
        assert primary.rpcs[0][1]["p_ttl_seconds"] == openapi_service.CONFIRM_LOCK_TTL_SECONDS


class TestEsitiChiamata:
    """Esiti della chiamata a pagamento: nessuno di questi mette dati in staging."""

    async def test_piva_non_trovata(self):
        primary = FakePrimary(selects={"company_profiles": [COMPANY_ROW]})
        with pytest.raises(NotFoundError):
            await openapi_service.preview_import(
                primary, None, fake_openapi(error=OpenapiInvalidIdError()), _active(), PIVA
            )
        events = primary.ops_for("api_usage_events", "insert")
        assert events[0]["outcome"] == "error"
        assert primary.ops_for("company_import_drafts", "upsert") == []
        assert ("fn_release_import_lock", {"p_parent_id": USER["id"]}) in primary.rpcs

    async def test_timeout_ledger_e_lock_non_rilasciato(self):
        primary = FakePrimary(selects={"company_profiles": [COMPANY_ROW]})
        with pytest.raises(OpenapiTimeoutError):
            await openapi_service.preview_import(
                primary, None, fake_openapi(error=OpenapiTimeoutError()), _active(), PIVA
            )
        events = primary.ops_for("api_usage_events", "insert")
        assert events[0]["outcome"] == "timeout_unknown"
        assert events[0]["cost_cents"] == 30  # possibile addebito
        released = [name for name, _ in primary.rpcs if name == "fn_release_import_lock"]
        assert released == []  # scade da solo: protegge dal doppio addebito

    async def test_mismatch_piva_non_va_in_staging(self):
        data = it_full_payload()
        data["companyDetails"]["vatCode"] = ALTRA_PIVA
        data["companyDetails"]["taxCode"] = ALTRA_PIVA
        primary = FakePrimary(selects={"company_profiles": [COMPANY_ROW]})
        with pytest.raises(OpenapiUpstreamError):
            await openapi_service.preview_import(
                primary, None, fake_openapi(result=data), _active(), PIVA
            )
        assert primary.ops_for("company_import_drafts", "upsert") == []
        events = primary.ops_for("api_usage_events", "insert")
        assert events[0]["request_meta"]["mismatch"] is True
        assert ("fn_release_import_lock", {"p_parent_id": USER["id"]}) in primary.rpcs


class TestAnteprima:
    async def test_non_scrive_nulla_sui_dati_azienda(self):
        primary = FakePrimary(selects={"company_profiles": [COMPANY_ROW]})
        preview = await openapi_service.preview_import(
            primary, None, fake_openapi(result=it_full_payload()), _active(), PIVA
        )
        # ledger success con costo pieno, lock rilasciato
        events = primary.ops_for("api_usage_events", "insert")
        assert events[0]["outcome"] == "success" and events[0]["cost_cents"] == 30
        assert ("fn_release_import_lock", {"p_parent_id": USER["id"]}) in primary.rpcs
        # SOLA LETTURA: il payload finisce in staging, nient'altro viene toccato
        draft = primary.ops_for("company_import_drafts", "upsert")[0]
        assert draft["partita_iva"] == PIVA and draft["raw"] == it_full_payload()
        assert primary.ops_for("company_profiles", "update") == []
        assert primary.ops_for("company_data", "upsert") == []
        assert primary.ops_for("company_people", "insert") == []
        assert primary.ops_for("audit_log", "insert") == []
        # «è la mia azienda?»
        assert preview.reused is False and preview.sandbox is False
        assert preview.azienda.partita_iva == PIVA
        assert preview.azienda.ragione_sociale.startswith("ENTE RICERCA")
        assert preview.azienda.stato_impresa == "Attiva"
        assert preview.azienda.legale_rappresentante
        assert preview.azienda.numero_persone > 0
        # «cosa verrà scritto?» — gli stessi campi che scriverà la conferma
        assert "ateco_id" in preview.autofill.applied
        assert preview.autofill.conflicts == [
            {"campo": "ragione_sociale", "valore_attuale": "ACME",
             "valore_certificato": it_full_payload()["companyDetails"]["companyName"]}
        ]

    async def test_draft_valido_riusato_gratis(self):
        """Chi annulla e ci ripensa non ripaga: nessuna chiamata, nessun lock."""
        primary = FakePrimary(
            selects={
                "company_profiles": [COMPANY_ROW],
                "company_import_drafts": [draft_row(eta_minuti=1)],
            }
        )
        called = []

        async def it_full(piva):
            called.append(piva)

        openapi = SimpleNamespace(enabled=True, sandbox=False, it_full=it_full)
        preview = await openapi_service.preview_import(primary, None, openapi, _active(), PIVA)
        assert preview.reused is True
        assert called == []
        assert primary.rpcs == []
        assert primary.ops_for("api_usage_events", "insert") == []
        assert primary.ops_for("company_import_drafts", "upsert") == []

    async def test_draft_scaduto_si_ripaga(self):
        primary = FakePrimary(
            selects={
                "company_profiles": [COMPANY_ROW],
                "company_import_drafts": [draft_row(eta_minuti=60, ttl_minuti=30)],
            }
        )
        preview = await openapi_service.preview_import(
            primary, None, fake_openapi(result=it_full_payload()), _active(), PIVA
        )
        assert preview.reused is False
        assert primary.ops_for("api_usage_events", "insert")[0]["cost_cents"] == 30

    async def test_sandbox_costo_zero(self):
        primary = FakePrimary(selects={"company_profiles": [COMPANY_ROW]})
        preview = await openapi_service.preview_import(
            primary, None, fake_openapi(result=it_full_payload(), sandbox=True), _active(), PIVA
        )
        assert preview.sandbox is True
        assert primary.ops_for("api_usage_events", "insert")[0]["cost_cents"] == 0
        assert primary.ops_for("company_import_drafts", "upsert")[0]["sandbox"] is True


class TestConferma:
    async def test_flusso_completo(self):
        primary = FakePrimary(
            selects={
                "company_profiles": [COMPANY_ROW],
                "company_import_drafts": [draft_row()],
            }
        )
        result = await openapi_service.confirm_import(primary, None, _active(), PIVA)
        # gratuita: nessuna chiamata a pagamento, nessuna riga nel registro consumi
        assert primary.ops_for("api_usage_events", "insert") == []
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
        # draft consumato: una seconda conferma non trova nulla
        assert primary.ops_for("company_import_drafts", "delete")
        # lock rilasciato
        assert ("fn_release_import_lock", {"p_parent_id": USER["id"]}) in primary.rpcs
        # risultato: identico a quello del vecchio import in un colpo solo
        assert result.sandbox is False
        assert result.dossier["anagrafica"]["stato"] == "Attiva"
        assert result.autofill.conflicts == [
            {"campo": "ragione_sociale", "valore_attuale": "ACME",
             "valore_certificato": it_full_payload()["companyDetails"]["companyName"]}
        ]
        assert result.people[0].is_legale_rappresentante is True

    async def test_senza_draft_o_con_draft_scaduto(self):
        primary = FakePrimary(selects={"company_profiles": [COMPANY_ROW]})
        with pytest.raises(AppError) as exc:
            await openapi_service.confirm_import(primary, None, _active(), PIVA)
        assert exc.value.code == "draft_not_found"

        scaduto = FakePrimary(
            selects={
                "company_profiles": [COMPANY_ROW],
                "company_import_drafts": [draft_row(eta_minuti=60, ttl_minuti=30)],
            }
        )
        with pytest.raises(AppError) as exc:
            await openapi_service.confirm_import(scaduto, None, _active(), PIVA)
        assert exc.value.code == "draft_not_found"
        assert scaduto.ops_for("company_data", "upsert") == []

    async def test_draft_di_unaltra_piva_non_viene_scritto(self):
        """Guardia contro la scrittura dei dati di un'azienda diversa."""
        primary = FakePrimary(
            selects={
                "company_profiles": [COMPANY_ROW],
                "company_import_drafts": [draft_row(ALTRA_PIVA)],
            }
        )
        with pytest.raises(AppError) as exc:
            await openapi_service.confirm_import(primary, None, _active(), PIVA)
        assert exc.value.code == "draft_mismatch"
        assert primary.ops_for("company_data", "upsert") == []
        assert primary.rpcs == []  # nemmeno il lock

    async def test_primo_import_crea_il_profilo_aziendale(self):
        primary = FakePrimary(
            selects={"company_profiles": [], "company_import_drafts": [draft_row()]}
        )

        # dopo l'insert la select deve trovare la riga
        original_execute = FakeQuery.execute

        async def execute(self):
            if self._table == "company_profiles" and self._op == "insert":
                self._primary.selects["company_profiles"] = [dict(COMPANY_ROW, ragione_sociale=None)]
            return await original_execute(self)

        FakeQuery.execute = execute
        try:
            # owner senza azienda: company_id None → bootstrap della prima azienda
            await openapi_service.confirm_import(primary, None, _active(company_id=None), PIVA)
        finally:
            FakeQuery.execute = original_execute

        created = primary.ops_for("company_profiles", "insert")[0]
        assert created["partita_iva"] == PIVA
        assert created["ragione_sociale"].startswith("ENTE RICERCA")

    async def test_sandbox_propagato_dal_draft(self):
        primary = FakePrimary(
            selects={
                "company_profiles": [COMPANY_ROW],
                "company_import_drafts": [draft_row(sandbox=True)],
            }
        )
        result = await openapi_service.confirm_import(primary, None, _active(), PIVA)
        assert result.sandbox is True
        assert primary.ops_for("company_data", "upsert")[0]["sandbox"] is True


class TestDossier:
    async def test_mai_importato(self):
        primary = FakePrimary(selects={"company_profiles": [COMPANY_ROW]})
        resp = await openapi_service.get_dossier(primary, _active())
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
        resp = await openapi_service.get_dossier(primary, _active())
        assert resp.imported is True and resp.editable is True
        assert resp.dossier["anagrafica"]["denominazione"].startswith("ENTE")
        assert resp.people[0].nome == "MICHELE"
        assert resp.derived["classe_dimensionale"] == "micro"

    async def test_figlio_attivo_legge_la_famiglia(self):
        # Il resolver dà editable=False (figlio attivo); qui il titolare non ha
        # ancora importato → company_id None → imported=False.
        primary = FakePrimary(selects={"company_profiles": []})
        resp = await openapi_service.get_dossier(
            primary, _active(company_id=None, editable=False)
        )
        assert resp.editable is False and resp.imported is False
