"""Test del flusso di import IT-full a due fasi — anteprima (a pagamento) e
conferma (gratuita) — con client PostgREST e openapi finti.

Il tema di fondo è il denaro: ogni chiamata IT-full costa credito reale.
I test presidiano i tre punti in cui si può pagare due volte (cooldown, lock,
riuso del draft) e l'unico in cui si può pagare per nulla (il draft scaduto).
Da WP1 anche IT-advanced (storico dei bilanci) nell'anteprima: si paga solo
quando serve, mai due volte, e non blocca mai l'import."""

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from postgrest.exceptions import APIError

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
from app.core.errors import UpstreamError
from app.services import openapi_service
from app.services.bilanci_mapping import MAPPING_BILANCI_VERSIONE

FIXTURES = Path(__file__).parent / "fixtures" / "openapi"
USER = {"id": "a0000000-0000-0000-0000-000000000001", "role": "cliente", "is_active": True}
PIVA = "14061981008"
ACTIVE_COMPANY = "c-openapi"
# Token restituito da fn_acquire_import_lock_token nel finto primario.
LOCK_TOKEN = "70000000-0000-0000-0000-00000000cafe"
RILASCIO = ("fn_release_import_lock_token", {"p_parent_id": USER["id"], "p_token": LOCK_TOKEN})


def _active(company_id: str | None = ACTIVE_COMPANY, editable: bool = True) -> ActiveCompany:
    return ActiveCompany(company_id=company_id, owner_id=USER["id"], editable=editable)


ALTRA_PIVA = "00000000000"  # checksum valido, azienda diversa


def it_full_payload() -> dict:
    return json.loads((FIXTURES / "it_full_sample.json").read_text())["data"]


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def draft_row(
    piva: str = PIVA, *, payload: dict | None = None, sandbox: bool = False,
    eta_minuti: int = 0, ttl_minuti: int = 30,
    company_profile_id: str | None = ACTIVE_COMPANY, **advanced,
) -> dict:
    """Riga di `company_import_drafts`. `eta_minuti` > `ttl_minuti` = draft scaduto.
    `company_profile_id` = azienda per cui l'anteprima è stata pagata;
    `advanced` = colonne advanced_* (assenti = draft precedente alla 0032)."""
    fetched_at = datetime.now(timezone.utc) - timedelta(minutes=eta_minuti)
    return {
        "partita_iva": piva,
        "raw": it_full_payload() if payload is None else payload,
        "sandbox": sandbox,
        "fetched_at": fetched_at.isoformat(),
        "expires_at": (fetched_at + timedelta(minutes=ttl_minuti)).isoformat(),
        "company_profile_id": company_profile_id,
        **{f"advanced_{k}": v for k, v in advanced.items()},
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
    """Registra le operazioni; `selects` configura le risposte alle SELECT.

    RPC: il lock con token (`lock=False` = occupato), la quota giornaliera
    (`quota`: True/False) e la registrazione dei bilanci (risponde con gli
    anni ricevuti). `rpc_errors` fa fallire una RPC per nome."""

    def __init__(self, selects: dict | None = None, lock: bool = True, quota: bool = True):
        self.selects = selects or {}
        self.lock = lock
        self.quota = quota
        self.rpc_errors: dict[str, Exception] = {}
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
                if name in primary.rpc_errors:
                    raise primary.rpc_errors[name]
                if name == "fn_acquire_import_lock_token":
                    return SimpleNamespace(data=LOCK_TOKEN if primary.lock else None)
                if name == "fn_openapi_prenota_operazione":
                    return SimpleNamespace(data=primary.quota)
                if name == "fn_bilanci_registra_fonte":
                    return SimpleNamespace(
                        data={"anni": sorted({r["anno"] for r in params["p_righe"]})}
                    )
                return SimpleNamespace(data=None)

        return _Rpc()

    # helper d'ispezione
    def ops_for(self, table: str, op: str) -> list:
        return [payload for t, o, payload in self.ops if t == table and o == op]

    def rpc_names(self) -> list[str]:
        return [name for name, _ in self.rpcs]


def fake_openapi(
    result=None, error: Exception | None = None, enabled=True, sandbox=False,
    advanced=None, advanced_error: Exception | None = None,
):
    """openapi finto. `calls` registra le chiamate a pagamento, per prodotto
    (il tetto di IT-advanced arrotondato: il mint, gratuito, ne consuma un
    istante)."""
    calls: dict[str, list] = {"it_full": [], "it_advanced": []}

    async def it_full(piva):
        calls["it_full"].append(piva)
        if error:
            raise error
        return result

    async def prepara_token(gruppo):
        return None

    async def it_advanced(piva, *, timeout_s):
        calls["it_advanced"].append((piva, round(timeout_s)))
        if advanced_error:
            raise advanced_error
        return advanced

    return SimpleNamespace(
        enabled=enabled, sandbox=sandbox, it_full=it_full, it_advanced=it_advanced,
        prepara_token=prepara_token, calls=calls,
    )


@pytest.fixture(autouse=True)
def stub_settings(monkeypatch):
    for key, value in {
        "PRIMARY_SUPABASE_URL": "https://dummy.supabase.co",
        "PRIMARY_SUPABASE_SERVICE_ROLE_KEY": "k",
        "SECONDARY_SUPABASE_URL": "https://d2.supabase.co",
        "SECONDARY_SUPABASE_ANON_KEY": "k",
        "COMPANY_IMPORT_COOLDOWN_MINUTES": "10",
        "COMPANY_IMPORT_DRAFT_TTL_MINUTES": "30",
        # Storico acceso: i test di IT-advanced nell'anteprima lo presuppongono;
        # a storico spento, TestStoricoSpento.
        "BILANCI_STORICO_ATTIVO": "true",
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
        # un lock occupato non consuma la quota giornaliera
        assert "fn_openapi_prenota_operazione" not in primary.rpc_names()
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
        assert RILASCIO in primary.rpcs

    async def test_timeout_ledger_e_lock_non_rilasciato(self):
        primary = FakePrimary(selects={"company_profiles": [COMPANY_ROW]})
        with pytest.raises(OpenapiTimeoutError):
            await openapi_service.preview_import(
                primary, None, fake_openapi(error=OpenapiTimeoutError()), _active(), PIVA
            )
        events = primary.ops_for("api_usage_events", "insert")
        assert events[0]["outcome"] == "timeout_unknown"
        assert events[0]["cost_cents"] == 30  # possibile addebito
        released = [name for name, _ in primary.rpcs if name.startswith("fn_release")]
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
        assert RILASCIO in primary.rpcs


class TestAnteprima:
    async def test_non_scrive_nulla_sui_dati_azienda(self):
        primary = FakePrimary(selects={"company_profiles": [COMPANY_ROW]})
        preview = await openapi_service.preview_import(
            primary, None, fake_openapi(result=it_full_payload()), _active(), PIVA
        )
        # ledger success con costo pieno, lock rilasciato
        events = primary.ops_for("api_usage_events", "insert")
        assert events[0]["outcome"] == "success" and events[0]["cost_cents"] == 30
        assert RILASCIO in primary.rpcs
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
        [audit] = primary.ops_for("audit_log", "insert")
        assert audit["action"] == "company.imported"
        # P.IVA mascherata anche nell'audit (per una ditta individuale è un
        # dato personale; quella completa sta in company_data.piva_fetched)
        assert audit["payload"]["piva"] == "140*****008"
        assert PIVA not in json.dumps(audit)
        # draft consumato: una seconda conferma non trova nulla
        assert primary.ops_for("company_import_drafts", "delete")
        # lock rilasciato
        assert RILASCIO in primary.rpcs
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
        # anteprima pagata quando l'azienda non esisteva ancora: draft senza azienda
        primary = FakePrimary(
            selects={
                "company_profiles": [],
                "company_import_drafts": [draft_row(company_profile_id=None)],
            }
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


# ============================================================ WP1: bilanci

PIVA_BIL = "09876543217"  # P.IVA fittizia della fixture sintetica
PIVA_BIL_MASCHERATA = "098*****217"
TENTATO_AT = "2026-09-28T09:00:00+00:00"


def it_full_bilanci() -> dict:
    return json.loads((FIXTURES / "it_full_bilanci_sintetico.json").read_text())["data"]


def it_advanced_dato() -> dict:
    return json.loads((FIXTURES / "it_advanced_sintetico.json").read_text())["data"][0]


COMPANY_BIL = {**COMPANY_ROW, "partita_iva": PIVA_BIL, "ragione_sociale": "ALFA"}


def eventi(primary, service: str) -> list[dict]:
    return [e for e in primary.ops_for("api_usage_events", "insert") if e["service"] == service]


def registrazioni(primary) -> dict[str, dict]:
    """Chiamate a fn_bilanci_registra_fonte per fonte."""
    return {p["p_fonte"]: p for name, p in primary.rpcs if name == "fn_bilanci_registra_fonte"}


def upsert_stato(primary) -> list[dict]:
    return primary.ops_for("company_financials_stato", "upsert")


class TestItAdvancedNienteDoppiaSpesa:
    """IT-advanced (0,10 €) non si paga MAI quando è inutile."""

    @pytest.mark.parametrize(
        "errore", [OpenapiInvalidIdError(), OpenapiTimeoutError(), OpenapiUpstreamError()]
    )
    async def test_it_full_fallito(self, errore):
        primary = FakePrimary(selects={"company_profiles": [COMPANY_BIL]})
        openapi = fake_openapi(error=errore, advanced=it_advanced_dato())
        with pytest.raises((AppError, OpenapiInvalidIdError)):
            await openapi_service.preview_import(primary, None, openapi, _active(), PIVA_BIL)
        assert openapi.calls["it_full"] == [PIVA_BIL]
        assert openapi.calls["it_advanced"] == []
        assert eventi(primary, "IT-advanced") == []
        assert primary.ops_for("company_import_drafts", "upsert") == []

    async def test_mismatch_di_it_full(self):
        data = it_full_bilanci()
        data["companyDetails"]["vatCode"] = ALTRA_PIVA
        data["companyDetails"]["taxCode"] = ALTRA_PIVA
        primary = FakePrimary(selects={"company_profiles": [COMPANY_BIL]})
        openapi = fake_openapi(result=data, advanced=it_advanced_dato())
        with pytest.raises(OpenapiUpstreamError):
            await openapi_service.preview_import(primary, None, openapi, _active(), PIVA_BIL)
        assert openapi.calls["it_advanced"] == []
        assert RILASCIO in primary.rpcs

    async def test_draft_riusato(self):
        """Il riuso è gratis per ENTRAMBI i prodotti: lo storico viene dal draft."""
        draft = draft_row(
            PIVA_BIL, payload=it_full_bilanci(), eta_minuti=1,
            esito="ok", motivo=None, raw=it_advanced_dato(), tentato_at=TENTATO_AT,
        )
        primary = FakePrimary(
            selects={"company_profiles": [COMPANY_BIL], "company_import_drafts": [draft]}
        )
        openapi = fake_openapi(result=it_full_bilanci(), advanced=it_advanced_dato())
        preview = await openapi_service.preview_import(primary, None, openapi, _active(), PIVA_BIL)
        assert preview.reused is True
        assert openapi.calls == {"it_full": [], "it_advanced": []}
        assert primary.rpcs == []
        assert preview.bilanci.stato == "disponibili"
        assert preview.bilanci.anni == [2017, 2018, 2019, 2020, 2021, 2022]

    async def test_societa_di_persone(self):
        data = it_full_bilanci()
        data["legalForm"]["legalForm"] = {"code": "SP", "description": "Partnership"}
        primary = FakePrimary(selects={"company_profiles": [COMPANY_BIL]})
        openapi = fake_openapi(result=data, advanced=it_advanced_dato())
        preview = await openapi_service.preview_import(primary, None, openapi, _active(), PIVA_BIL)
        assert openapi.calls["it_advanced"] == []
        draft = primary.ops_for("company_import_drafts", "upsert")[0]
        assert draft["advanced_esito"] == "saltato"
        assert draft["advanced_motivo"] == "forma_senza_bilancio"
        assert draft["advanced_tentato_at"] is None  # niente cooldown: non si è pagato
        # l'esercizio di IT-full la conferma lo registra comunque
        assert preview.bilanci.model_dump() == {
            "stato": "disponibili", "motivo": "forma_senza_bilancio", "anni": [2021]
        }

    async def test_spa_nel_dettaglio_non_e_una_societa_di_persone(self):
        # 'SP' in detailedLegalForm è la SpA: conta solo il codice di primo livello.
        data = it_full_bilanci()
        data["legalForm"]["detailedLegalForm"] = {"code": "SP", "description": "SpA"}
        primary = FakePrimary(selects={"company_profiles": [COMPANY_BIL]})
        openapi = fake_openapi(result=data, advanced=it_advanced_dato())
        await openapi_service.preview_import(primary, None, openapi, _active(), PIVA_BIL)
        assert len(openapi.calls["it_advanced"]) == 1

    async def test_piva_diversa_da_quella_del_profilo(self):
        # Il profilo dichiara un'altra P.IVA (nessun import ancora): IT-full sì,
        # lo storico no — lo si recupera dopo, a P.IVA chiarita.
        primary = FakePrimary(selects={"company_profiles": [COMPANY_ROW]})
        openapi = fake_openapi(result=it_full_bilanci(), advanced=it_advanced_dato())
        preview = await openapi_service.preview_import(primary, None, openapi, _active(), PIVA_BIL)
        assert openapi.calls["it_full"] == [PIVA_BIL]
        assert openapi.calls["it_advanced"] == []
        draft = primary.ops_for("company_import_drafts", "upsert")[0]
        assert (draft["advanced_esito"], draft["advanced_motivo"]) == ("saltato", "piva_diversa")
        assert preview.bilanci.motivo == "piva_diversa"

    async def test_tempo_insufficiente_dopo_it_full(self, monkeypatch):
        """IT-full durato 260 s: avviare IT-advanced sforerebbe la catena
        server 277 s < frontend 290 s < lock 330 s."""
        reale = time.monotonic
        salto = {"secondi": 0.0}
        openapi = fake_openapi(result=it_full_bilanci(), advanced=it_advanced_dato())
        it_full_originale = openapi.it_full

        async def it_full_lento(piva):
            data = await it_full_originale(piva)
            salto["secondi"] = 260.0
            return data

        openapi.it_full = it_full_lento
        # Salto in avanti (l'orologio resta monotono): è il tempo "passato" in IT-full.
        monkeypatch.setattr(openapi_service.time, "monotonic", lambda: reale() + salto["secondi"])
        primary = FakePrimary(selects={"company_profiles": [COMPANY_BIL]})
        preview = await openapi_service.preview_import(primary, None, openapi, _active(), PIVA_BIL)
        assert openapi.calls["it_advanced"] == []
        draft = primary.ops_for("company_import_drafts", "upsert")[0]
        assert draft["advanced_motivo"] == "tempo_insufficiente"
        assert (preview.bilanci.stato, preview.bilanci.anni) == ("disponibili", [2021])
        assert preview.bilanci.motivo == "tempo_insufficiente"
        assert eventi(primary, "IT-advanced") == []

    async def test_entro_il_budget_di_tempo_si_chiama(self):
        primary = FakePrimary(selects={"company_profiles": [COMPANY_BIL]})
        openapi = fake_openapi(result=it_full_bilanci(), advanced=it_advanced_dato())
        await openapi_service.preview_import(primary, None, openapi, _active(), PIVA_BIL)
        # tetto rigido della singola chiamata
        assert openapi.calls["it_advanced"] == [
            (PIVA_BIL, openapi_service.IT_ADVANCED_TIMEOUT_SECONDS)
        ]


class TestItAdvancedEsiti:
    """Qualunque esito di IT-advanced lascia valida l'anteprima IT-full."""

    async def test_ok_storico_in_staging(self):
        primary = FakePrimary(selects={"company_profiles": [COMPANY_BIL]})
        openapi = fake_openapi(result=it_full_bilanci(), advanced=it_advanced_dato())
        preview = await openapi_service.preview_import(primary, None, openapi, _active(), PIVA_BIL)

        # UN solo upsert del draft, con entrambi i payload e l'azienda
        [draft] = primary.ops_for("company_import_drafts", "upsert")
        assert draft["raw"] == it_full_bilanci()
        assert draft["advanced_raw"] == it_advanced_dato()
        assert draft["advanced_esito"] == "ok" and draft["advanced_motivo"] is None
        assert draft["advanced_tentato_at"]
        assert draft["company_profile_id"] == ACTIVE_COMPANY
        # registro: IT-full + IT-advanced a costo pieno
        [adv] = eventi(primary, "IT-advanced")
        assert adv["outcome"] == "success" and adv["cost_cents"] == 10
        assert adv["request_meta"]["anni"] == [2017, 2018, 2019, 2020, 2021, 2022]
        assert eventi(primary, "IT-full")[0]["cost_cents"] == 30
        # la quota giornaliera conta UNA operazione per anteprima
        assert primary.rpc_names().count("fn_openapi_prenota_operazione") == 1
        assert RILASCIO in primary.rpcs
        # anteprima: storico (2017-2022) ∪ esercizio di IT-full (2021)
        assert preview.bilanci.stato == "disponibili"
        assert preview.bilanci.anni == [2017, 2018, 2019, 2020, 2021, 2022]
        assert preview.bilanci.motivo is None
        # nessuna scrittura sui dati aziendali, bilanci compresi
        assert "fn_bilanci_registra_fonte" not in primary.rpc_names()
        assert upsert_stato(primary) == []

    async def test_timeout_anteprima_200_e_lock_rilasciato(self):
        primary = FakePrimary(selects={"company_profiles": [COMPANY_BIL]})
        openapi = fake_openapi(
            result=it_full_bilanci(), advanced_error=OpenapiTimeoutError()
        )
        preview = await openapi_service.preview_import(primary, None, openapi, _active(), PIVA_BIL)
        # l'anteprima IT-full resta valida
        assert preview.azienda.partita_iva == PIVA_BIL
        # l'esercizio di IT-full c'è comunque; manca lo storico, e si dice perché
        assert preview.bilanci.model_dump() == {
            "stato": "disponibili", "motivo": "esito_incerto", "anni": [2021]
        }
        # esito ignoto: costo pieno a registro, mai retry
        [adv] = eventi(primary, "IT-advanced")
        assert adv["outcome"] == "timeout_unknown" and adv["cost_cents"] == 10
        assert len(openapi.calls["it_advanced"]) == 1
        # il lock si rilascia (col token), altrimenti la conferma resterebbe
        # bloccata per 5 minuti: il doppio addebito lo ferma il cooldown
        assert RILASCIO in primary.rpcs
        [draft] = primary.ops_for("company_import_drafts", "upsert")
        assert (draft["advanced_esito"], draft["advanced_motivo"]) == ("timeout", "esito_incerto")
        assert draft["advanced_raw"] is None
        assert draft["advanced_tentato_at"]  # base del cooldown di «Recupera»

    @pytest.mark.parametrize(
        ("status", "outcome", "costo"),
        [(200, "success", 10), (204, "success", 10), (404, "error", 0)],
    )
    async def test_nessun_bilancio(self, status, outcome, costo):
        from app.clients.openapi import OpenapiNessunDatoError

        primary = FakePrimary(selects={"company_profiles": [COMPANY_BIL]})
        openapi = fake_openapi(
            result=it_full_bilanci(), advanced_error=OpenapiNessunDatoError(status)
        )
        preview = await openapi_service.preview_import(primary, None, openapi, _active(), PIVA_BIL)
        assert preview.bilanci.motivo == "nessun_bilancio"
        [adv] = eventi(primary, "IT-advanced")
        assert (adv["outcome"], adv["cost_cents"]) == (outcome, costo)

    async def test_storico_di_unaltra_impresa_scartato(self):
        dato = it_advanced_dato()
        dato["vatCode"] = dato["taxCode"] = ALTRA_PIVA
        primary = FakePrimary(selects={"company_profiles": [COMPANY_BIL]})
        openapi = fake_openapi(result=it_full_bilanci(), advanced=dato)
        preview = await openapi_service.preview_import(primary, None, openapi, _active(), PIVA_BIL)
        [draft] = primary.ops_for("company_import_drafts", "upsert")
        assert draft["advanced_esito"] == "mismatch"
        assert draft["advanced_raw"] is None  # i dati altrui non entrano MAI
        [adv] = eventi(primary, "IT-advanced")
        assert adv["outcome"] == "success" and adv["request_meta"]["mismatch"] is True
        assert preview.bilanci.motivo == "dati_non_corrispondenti"

    async def test_mint_del_gruppo_fallito(self):
        from app.clients.openapi import OpenapiNonInviataError

        primary = FakePrimary(selects={"company_profiles": [COMPANY_BIL]})
        openapi = fake_openapi(result=it_full_bilanci(), advanced_error=OpenapiNonInviataError())
        preview = await openapi_service.preview_import(primary, None, openapi, _active(), PIVA_BIL)
        assert preview.bilanci.motivo == "errore_provider"
        [adv] = eventi(primary, "IT-advanced")
        assert (adv["outcome"], adv["cost_cents"]) == ("error", 0)

    async def test_sandbox_costo_zero(self):
        primary = FakePrimary(selects={"company_profiles": [COMPANY_BIL]})
        openapi = fake_openapi(result=it_full_bilanci(), advanced=it_advanced_dato(), sandbox=True)
        await openapi_service.preview_import(primary, None, openapi, _active(), PIVA_BIL)
        assert eventi(primary, "IT-advanced")[0]["cost_cents"] == 0

    async def test_senza_bilanci_ne_storico_non_disponibili(self):
        # Nessun esercizio da IT-full (fixture reale: associazione) e storico
        # assente: allora sì «non disponibili», con il motivo.
        primary = FakePrimary(selects={"company_profiles": [COMPANY_ROW]})
        openapi = fake_openapi(
            result=it_full_payload(), advanced_error=OpenapiTimeoutError()
        )
        preview = await openapi_service.preview_import(primary, None, openapi, _active(), PIVA)
        assert preview.bilanci.model_dump() == {
            "stato": "non_disponibili", "motivo": "esito_incerto", "anni": []
        }


class TestQuotaGiornaliera:
    """Q10: tetto fail-closed delle operazioni openapi a pagamento."""

    async def test_prenotazione_per_owner(self):
        primary = FakePrimary(selects={"company_profiles": [COMPANY_ROW]})
        await openapi_service.preview_import(
            primary, None, fake_openapi(result=it_full_payload()), _active(), PIVA
        )
        assert (
            "fn_openapi_prenota_operazione",
            {"p_owner": USER["id"], "p_per_azienda": 3},
        ) in primary.rpcs
        # dopo il lock: l'ordine delle RPC è acquire → prenota
        nomi = primary.rpc_names()
        assert nomi.index("fn_acquire_import_lock_token") < nomi.index(
            "fn_openapi_prenota_operazione"
        )

    async def test_esaurita_429_senza_chiamata(self):
        primary = FakePrimary(selects={"company_profiles": [COMPANY_ROW]}, quota=False)
        openapi = fake_openapi(result=it_full_payload())
        with pytest.raises(AppError) as exc:
            await openapi_service.preview_import(primary, None, openapi, _active(), PIVA)
        assert (exc.value.status_code, exc.value.code) == (429, "limite_giornaliero_openapi")
        assert openapi.calls == {"it_full": [], "it_advanced": []}
        assert primary.ops_for("api_usage_events", "insert") == []
        assert RILASCIO in primary.rpcs

    async def test_errore_rpc_fail_closed(self):
        primary = FakePrimary(selects={"company_profiles": [COMPANY_ROW]})
        primary.rpc_errors["fn_openapi_prenota_operazione"] = APIError(
            {"message": "boom", "code": "XX000", "hint": None, "details": None}
        )
        openapi = fake_openapi(result=it_full_payload())
        with pytest.raises(UpstreamError):
            await openapi_service.preview_import(primary, None, openapi, _active(), PIVA)
        assert openapi.calls["it_full"] == []  # quota non verificabile = nessuna spesa
        assert RILASCIO in primary.rpcs


class TestPivaLegataAllAzienda:
    """Q10: dopo il primo import la P.IVA resta legata all'azienda."""

    async def test_piva_diversa_da_importata_409(self):
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        primary = FakePrimary(
            selects={
                "company_profiles": [COMPANY_ROW],
                "company_data": [{"piva_fetched": PIVA, "fetched_at": old, "fetch_count": 1}],
            }
        )
        openapi = fake_openapi(result=it_full_bilanci())
        with pytest.raises(AppError) as exc:
            await openapi_service.preview_import(primary, None, openapi, _active(), PIVA_BIL)
        assert (exc.value.status_code, exc.value.code) == (409, "piva_diversa_da_importata")
        assert openapi.calls["it_full"] == []
        assert primary.rpcs == []  # nemmeno lock o quota

    async def test_stessa_piva_si_aggiorna(self):
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        primary = FakePrimary(
            selects={
                "company_profiles": [COMPANY_ROW],
                "company_data": [{"piva_fetched": PIVA, "fetched_at": old, "fetch_count": 1}],
            }
        )
        openapi = fake_openapi(result=it_full_payload())
        preview = await openapi_service.preview_import(primary, None, openapi, _active(), PIVA)
        assert openapi.calls["it_full"] == [PIVA] and preview.reused is False


class TestDraftPerAzienda:
    """B4: il draft è per owner, ma vale solo per l'azienda per cui è stato pagato."""

    async def test_draft_di_unaltra_azienda_non_riusato(self):
        altrui = draft_row(eta_minuti=15, company_profile_id="c-altra-azienda")
        primary = FakePrimary(
            selects={"company_profiles": [COMPANY_ROW], "company_import_drafts": [altrui]}
        )
        openapi = fake_openapi(result=it_full_payload())
        preview = await openapi_service.preview_import(primary, None, openapi, _active(), PIVA)
        assert preview.reused is False
        assert openapi.calls["it_full"] == [PIVA]  # si ripaga: i dati sono per un'altra azienda
        [draft] = primary.ops_for("company_import_drafts", "upsert")
        assert draft["company_profile_id"] == ACTIVE_COMPANY

    async def test_conferma_draft_di_unaltra_azienda_409(self):
        primary = FakePrimary(
            selects={
                "company_profiles": [COMPANY_ROW],
                "company_import_drafts": [draft_row(company_profile_id="c-altra-azienda")],
            }
        )
        with pytest.raises(AppError) as exc:
            await openapi_service.confirm_import(primary, None, _active(), PIVA)
        assert exc.value.code == "draft_mismatch"
        assert primary.ops_for("company_data", "upsert") == []
        assert primary.rpcs == []

    async def test_draft_senza_azienda_non_vale_per_unazienda_esistente(self):
        primary = FakePrimary(
            selects={
                "company_profiles": [COMPANY_ROW],
                "company_import_drafts": [draft_row(company_profile_id=None)],
            }
        )
        with pytest.raises(AppError) as exc:
            await openapi_service.confirm_import(primary, None, _active(), PIVA)
        assert exc.value.code == "draft_mismatch"


class TestConfermaBilanci:
    def _primary(self, **draft_advanced) -> FakePrimary:
        draft = draft_row(PIVA_BIL, payload=it_full_bilanci(), **draft_advanced)
        return FakePrimary(
            selects={"company_profiles": [COMPANY_BIL], "company_import_drafts": [draft]}
        )

    async def test_persiste_it_full_e_it_advanced(self):
        primary = self._primary(
            esito="ok", motivo=None, raw=it_advanced_dato(), tentato_at=TENTATO_AT
        )
        await openapi_service.confirm_import(primary, None, _active(), PIVA_BIL)

        fonti = registrazioni(primary)
        full, adv = fonti["it_full"], fonti["it_advanced"]
        # it_full: l'esercizio di IT-full, senza sostituire gli anni precedenti
        assert full["p_sostituisci"] is False and full["p_riferimento"] == "import"
        assert full["p_company_id"] == COMPANY_BIL["id"]
        [riga] = full["p_righe"]
        assert riga["anno"] == 2021
        assert riga["valori"]["patrimonio_netto"] == "563473.00"  # stringhe, mai float
        # it_advanced: lo storico sostituisce il precedente; netWorth = UTILE
        assert adv["p_sostituisci"] is True
        assert [r["anno"] for r in adv["p_righe"]] == [2017, 2018, 2019, 2020, 2021, 2022]
        r2021 = next(r for r in adv["p_righe"] if r["anno"] == 2021)
        assert r2021["valori"]["risultato_esercizio"] == "469366.00"
        assert "patrimonio_netto" not in r2021["valori"]

        stato, versione = upsert_stato(primary)
        assert stato["advanced_esito"] == "ok"
        assert stato["advanced_raw"] == it_advanced_dato()
        assert stato["advanced_tentato_at"] == TENTATO_AT
        assert stato["advanced_fetched_at"] == TENTATO_AT
        assert stato["advanced_fetch_count"] == 1
        assert stato["advanced_piva"] == PIVA_BIL
        # versione del mapping SOLO dopo tutte le registrazioni
        assert versione == {
            "company_profile_id": COMPANY_BIL["id"], "mapping_versione": MAPPING_BILANCI_VERSIONE
        }
        # ordine: dati certificati prima, bilanci dopo
        tabelle = [t for t, o, _ in primary.ops if o == "upsert"]
        assert tabelle.index("company_data") < tabelle.index("company_financials_stato")
        # gratis: nessuna chiamata a pagamento né riga nel registro consumi
        assert primary.ops_for("api_usage_events", "insert") == []
        assert "fn_openapi_prenota_operazione" not in primary.rpc_names()
        assert primary.ops_for("company_import_drafts", "delete")

    async def test_rpc_bilanci_fallita_non_rompe_la_conferma(self):
        primary = self._primary(
            esito="ok", motivo=None, raw=it_advanced_dato(), tentato_at=TENTATO_AT
        )
        primary.rpc_errors["fn_bilanci_registra_fonte"] = APIError(
            {"message": "x", "code": "P0001", "hint": None, "details": "righe_non_valide"}
        )
        result = await openapi_service.confirm_import(primary, None, _active(), PIVA_BIL)
        assert result.sandbox is False
        assert primary.ops_for("company_data", "upsert")  # i dati certificati ci sono
        assert primary.ops_for("company_import_drafts", "delete")  # conferma completata
        assert RILASCIO in primary.rpcs
        # la versione NON sale: resta (anzi torna) vecchia, così la
        # rimappatura pigra registra gratis i bilanci alla prossima lettura
        versioni = [u["mapping_versione"] for u in upsert_stato(primary) if "mapping_versione" in u]
        assert MAPPING_BILANCI_VERSIONE not in versioni
        assert versioni == [0]
        # il raw pagato è comunque al sicuro nello stato
        assert upsert_stato(primary)[0]["advanced_raw"] == it_advanced_dato()

    async def test_draft_precedente_alla_0032(self):
        """Colonne advanced_* assenti: «non richiesto», nessuno stato scritto,
        solo l'esercizio di IT-full."""
        primary = self._primary()
        await openapi_service.confirm_import(primary, None, _active(), PIVA_BIL)
        assert set(registrazioni(primary)) == {"it_full"}
        assert all("advanced_esito" not in u for u in upsert_stato(primary))

    async def test_esito_saltato_non_tocca_il_cooldown(self):
        primary = self._primary(esito="saltato", motivo="tempo_insufficiente", tentato_at=None)
        await openapi_service.confirm_import(primary, None, _active(), PIVA_BIL)
        stato = upsert_stato(primary)[0]
        assert stato["advanced_esito"] == "saltato"
        # nessuna chiamata pagata: il tentativo precedente (se c'è) resta la base
        assert "advanced_tentato_at" not in stato
        assert "advanced_raw" not in stato
        assert set(registrazioni(primary)) == {"it_full"}


def stato_storico_ok(**over) -> dict:
    """`company_financials_stato` con lo storico già recuperato per PIVA_BIL."""
    return {
        "company_profile_id": COMPANY_BIL["id"], "advanced_esito": "ok",
        "advanced_motivo": None, "advanced_tentato_at": TENTATO_AT,
        "advanced_fetched_at": TENTATO_AT, "advanced_raw": it_advanced_dato(),
        "advanced_piva": PIVA_BIL, "advanced_sandbox": False, "advanced_fetch_count": 1,
        "mapping_versione": MAPPING_BILANCI_VERSIONE, **over,
    }


class TestStoricoGiaRecuperato:
    """Storico IT-advanced già recuperato per la stessa P.IVA: l'anteprima di
    un nuovo import lo riusa gratis (nessuna chiamata, nessuna riga nel
    registro consumi) e lo mostra come recuperato; la conferma non lo
    riscrive."""

    async def test_anteprima_riusa_lo_storico_senza_chiamata(self):
        primary = FakePrimary(selects={
            "company_profiles": [COMPANY_BIL], "company_financials_stato": [stato_storico_ok()],
        })
        openapi = fake_openapi(result=it_full_bilanci(), advanced=it_advanced_dato())
        preview = await openapi_service.preview_import(primary, None, openapi, _active(), PIVA_BIL)

        assert openapi.calls == {"it_full": [PIVA_BIL], "it_advanced": []}
        assert eventi(primary, "IT-advanced") == []
        assert len(eventi(primary, "IT-full")) == 1
        [draft] = primary.ops_for("company_import_drafts", "upsert")
        assert (draft["advanced_esito"], draft["advanced_motivo"]) == ("ok", None)
        assert draft["advanced_raw"] == it_advanced_dato()
        assert draft["advanced_tentato_at"] is None  # nessun cooldown: non si è pagato
        # la UI vede lo storico come recuperato, non «non richiesto»
        assert preview.bilanci.model_dump() == {
            "stato": "disponibili", "motivo": None, "anni": [2017, 2018, 2019, 2020, 2021, 2022],
        }
        assert upsert_stato(primary) == []

    @pytest.mark.parametrize(
        "over",
        [
            {"advanced_piva": ALTRA_PIVA},
            {"advanced_sandbox": True},
            {"advanced_esito": "non_disponibili", "advanced_motivo": "nessun_bilancio",
             "advanced_raw": None},
            {"advanced_raw": {**it_advanced_dato(), "vatCode": ALTRA_PIVA, "taxCode": ALTRA_PIVA}},
        ],
        ids=["altra_piva", "altro_ambiente", "esito_non_ok", "raw_di_altra_impresa"],
    )
    async def test_storico_non_riusabile_si_chiama(self, over):
        primary = FakePrimary(selects={
            "company_profiles": [COMPANY_BIL],
            "company_financials_stato": [stato_storico_ok(**over)],
        })
        openapi = fake_openapi(result=it_full_bilanci(), advanced=it_advanced_dato())
        await openapi_service.preview_import(primary, None, openapi, _active(), PIVA_BIL)
        assert len(openapi.calls["it_advanced"]) == 1
        [draft] = primary.ops_for("company_import_drafts", "upsert")
        assert draft["advanced_tentato_at"]

    async def test_stato_illeggibile_nessuna_chiamata(self, monkeypatch):
        from app.services import bilanci_service

        async def guasto(*_a, **_k):
            raise APIError({"message": "x", "code": "08006", "hint": None, "details": None})

        monkeypatch.setattr(bilanci_service, "storico_salvato", guasto)
        primary = FakePrimary(selects={"company_profiles": [COMPANY_BIL]})
        openapi = fake_openapi(result=it_full_bilanci(), advanced=it_advanced_dato())
        preview = await openapi_service.preview_import(primary, None, openapi, _active(), PIVA_BIL)
        assert openapi.calls["it_advanced"] == []
        assert eventi(primary, "IT-advanced") == []
        [draft] = primary.ops_for("company_import_drafts", "upsert")
        assert (draft["advanced_esito"], draft["advanced_motivo"]) == ("saltato", "errore_provider")
        assert draft["advanced_tentato_at"] is None
        assert preview.bilanci.anni == [2021]  # l'import IT-full resta valido

    async def test_conferma_non_riscrive_lo_storico(self):
        anteprima = FakePrimary(selects={
            "company_profiles": [COMPANY_BIL], "company_financials_stato": [stato_storico_ok()],
        })
        openapi = fake_openapi(result=it_full_bilanci(), advanced=it_advanced_dato())
        await openapi_service.preview_import(anteprima, None, openapi, _active(), PIVA_BIL)
        [draft] = anteprima.ops_for("company_import_drafts", "upsert")

        primary = FakePrimary(selects={
            "company_profiles": [COMPANY_BIL],
            "company_import_drafts": [draft],
            "company_financials_stato": [stato_storico_ok()],
        })
        await openapi_service.confirm_import(primary, None, _active(), PIVA_BIL)
        # solo l'esercizio di IT-full; le righe dello storico ci sono già
        fonti = registrazioni(primary)
        assert set(fonti) == {"it_full"} and fonti["it_full"]["p_sostituisci"] is False
        # nessun recupero fittizio nello stato, né la versione del mapping
        assert upsert_stato(primary) == []
        assert primary.ops_for("api_usage_events", "insert") == []
        assert primary.ops_for("company_import_drafts", "delete")

    async def test_conferma_it_full_fallito_versione_azzerata(self):
        draft = draft_row(
            PIVA_BIL, payload=it_full_bilanci(),
            esito="ok", motivo=None, raw=it_advanced_dato(), tentato_at=None,
        )
        primary = FakePrimary(selects={
            "company_profiles": [COMPANY_BIL], "company_import_drafts": [draft],
        })
        primary.rpc_errors["fn_bilanci_registra_fonte"] = APIError(
            {"message": "x", "code": "P0001", "hint": None, "details": "righe_non_valide"}
        )
        await openapi_service.confirm_import(primary, None, _active(), PIVA_BIL)
        assert upsert_stato(primary) == [
            {"company_profile_id": COMPANY_BIL["id"], "mapping_versione": 0}
        ]


class TestStoricoSpento:
    """BILANCI_STORICO_ATTIVO=false: l'anteprima non chiama MAI IT-advanced
    (esito None = «non richiesto», come un draft precedente alla 0032) e la
    conferma non tocca lo storico già salvato."""

    @pytest.fixture(autouse=True)
    def storico_spento(self, monkeypatch):
        from app.core.config import get_settings

        monkeypatch.setenv("BILANCI_STORICO_ATTIVO", "false")
        get_settings.cache_clear()

    async def test_it_advanced_non_chiamato(self):
        primary = FakePrimary(selects={"company_profiles": [COMPANY_BIL]})
        openapi = fake_openapi(result=it_full_bilanci(), advanced=it_advanced_dato())
        preview = await openapi_service.preview_import(primary, None, openapi, _active(), PIVA_BIL)

        assert openapi.calls == {"it_full": [PIVA_BIL], "it_advanced": []}
        assert eventi(primary, "IT-advanced") == []
        assert len(eventi(primary, "IT-full")) == 1
        assert primary.rpc_names().count("fn_openapi_prenota_operazione") == 1
        [draft] = primary.ops_for("company_import_drafts", "upsert")
        assert draft["advanced_esito"] is None and draft["advanced_motivo"] is None
        assert draft["advanced_tentato_at"] is None and draft["advanced_raw"] is None
        assert preview.bilanci.model_dump() == {
            "stato": "disponibili", "motivo": "non_richiesto", "anni": [2021]
        }
        assert upsert_stato(primary) == []
        assert RILASCIO in primary.rpcs

    async def test_draft_riusato(self):
        """Riuso del draft scritto a storico spento: gratis, IT-advanced non si
        chiama, lo storico resta «non richiesto»."""
        draft = draft_row(
            PIVA_BIL, payload=it_full_bilanci(), eta_minuti=1,
            esito=None, motivo=None, raw=None, tentato_at=None,
        )
        primary = FakePrimary(
            selects={"company_profiles": [COMPANY_BIL], "company_import_drafts": [draft]}
        )
        openapi = fake_openapi(result=it_full_bilanci(), advanced=it_advanced_dato())
        preview = await openapi_service.preview_import(primary, None, openapi, _active(), PIVA_BIL)
        assert preview.reused is True
        assert openapi.calls == {"it_full": [], "it_advanced": []}
        assert primary.rpcs == []  # né lock né quota
        assert primary.ops_for("api_usage_events", "insert") == []
        assert primary.ops_for("company_import_drafts", "upsert") == []
        assert preview.bilanci.model_dump() == {
            "stato": "disponibili", "motivo": "non_richiesto", "anni": [2021]
        }

    async def test_societa_di_persone_resta_saltata(self):
        data = it_full_bilanci()
        data["legalForm"]["legalForm"] = {"code": "SP", "description": "Partnership"}
        primary = FakePrimary(selects={"company_profiles": [COMPANY_BIL]})
        openapi = fake_openapi(result=data, advanced=it_advanced_dato())
        preview = await openapi_service.preview_import(primary, None, openapi, _active(), PIVA_BIL)
        assert openapi.calls["it_advanced"] == []
        [draft] = primary.ops_for("company_import_drafts", "upsert")
        assert (draft["advanced_esito"], draft["advanced_motivo"]) == (
            "saltato", "forma_senza_bilancio"
        )
        assert preview.bilanci.motivo == "forma_senza_bilancio"

    async def test_conferma_non_tocca_lo_storico_salvato(self):
        """Il draft scritto a storico spento, confermato su un'azienda con uno
        storico completo già recuperato: stato e righe it_advanced intatti."""
        anteprima = FakePrimary(selects={"company_profiles": [COMPANY_BIL]})
        openapi = fake_openapi(result=it_full_bilanci(), advanced=it_advanced_dato())
        await openapi_service.preview_import(anteprima, None, openapi, _active(), PIVA_BIL)
        [draft] = anteprima.ops_for("company_import_drafts", "upsert")

        stato_salvato = {
            "company_profile_id": COMPANY_BIL["id"], "advanced_esito": "ok",
            "advanced_motivo": None, "advanced_tentato_at": TENTATO_AT,
            "advanced_fetched_at": TENTATO_AT, "advanced_raw": it_advanced_dato(),
            "advanced_piva": PIVA_BIL, "advanced_fetch_count": 1,
            "mapping_versione": MAPPING_BILANCI_VERSIONE,
        }
        primary = FakePrimary(selects={
            "company_profiles": [COMPANY_BIL],
            "company_import_drafts": [draft],
            "company_financials_stato": [stato_salvato],
        })
        await openapi_service.confirm_import(primary, None, _active(), PIVA_BIL)

        # solo l'esercizio di IT-full, senza sostituire gli anni già registrati
        fonti = registrazioni(primary)
        assert set(fonti) == {"it_full"}
        assert fonti["it_full"]["p_sostituisci"] is False
        # nello stato al più la versione del mapping: esito, raw e date intatti
        assert all(
            not any(k.startswith("advanced_") for k in u) for u in upsert_stato(primary)
        )
        assert primary.ops_for("company_financials_stato", "update") == []
        assert primary.ops_for("company_financials_stato", "delete") == []
        assert primary.ops_for("api_usage_events", "insert") == []
        assert primary.ops_for("company_import_drafts", "delete")


class TestMascheramentoPiva:
    async def test_piva_mai_in_chiaro_nel_registro(self):
        primary = FakePrimary(selects={"company_profiles": [COMPANY_BIL]})
        openapi = fake_openapi(result=it_full_bilanci(), advanced=it_advanced_dato())
        await openapi_service.preview_import(primary, None, openapi, _active(), PIVA_BIL)
        events = primary.ops_for("api_usage_events", "insert")
        assert {e["service"] for e in events} == {"IT-full", "IT-advanced"}
        assert all(e["request_meta"]["piva"] == PIVA_BIL_MASCHERATA for e in events)
        assert PIVA_BIL not in json.dumps([e["request_meta"] for e in events])

    async def test_anche_nei_rami_di_errore_di_it_full(self):
        primary = FakePrimary(selects={"company_profiles": [COMPANY_ROW]})
        with pytest.raises(NotFoundError):
            await openapi_service.preview_import(
                primary, None, fake_openapi(error=OpenapiInvalidIdError()), _active(), PIVA
            )
        [event] = primary.ops_for("api_usage_events", "insert")
        assert event["request_meta"] == {"piva": "140*****008"}
