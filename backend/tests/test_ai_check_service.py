"""Test del servizio AI-check: guardie, quota, cooldown, lock, pipeline con
cache delle estrazioni, registro consumi e failsafe — con fake di primario,
catalogo e client Anthropic."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.clients.anthropic_ai import AiUsage
from app.core.errors import (
    AiNotConfiguredError,
    AiQuotaExceededError,
    AiTimeoutError,
    AppError,
    BadRequestError,
    ForbiddenError,
    NotFoundError,
)
from app.schemas.ai_check import ExtractionResult, MatchingResult
from app.services import ai_check_service
from app.services.ai_check_prompts import PROMPT_VERSION, build_bando_input
from app.services.ai_check_scoring import facet_prechecks
from app.services.bandi_service import normalize_contenuto

FIXTURES = Path(__file__).parent / "fixtures" / "ai_check"
USER = {"id": "a0000000-0000-0000-0000-000000000001", "nome": "Michele",
        "cognome": "Rossi", "codice_fiscale": None, "cf_verified_at": None,
        "role": "cliente", "is_active": True}
OWNER = USER["id"]
COMPANY_ID = "c0000000-0000-0000-0000-000000000001"


def load_bando() -> dict:
    bando = json.loads((FIXTURES / "bando_flash.json").read_text())
    bando["contenuto"] = normalize_contenuto(bando.get("contenuto"))
    return bando


COMPANY_ROW = {
    "id": COMPANY_ID, "ragione_sociale": "ACME Srl", "partita_iva": "01234567890",
    "codice_fiscale": None, "forma_giuridica": None, "ateco_id": None,
    "ateco_codice": "62.01", "ateco_descrizione": None, "settore_id": None,
    "settore_nome": None, "regione_id": 12, "regione_nome": "Lazio",
    "anno_fondazione": None, "indirizzo": None, "comune": None, "provincia": None,
    "cap": None, "classe_dimensionale": None, "numero_dipendenti": None,
    "fascia_fatturato": None, "pec": None, "telefono": None, "sito_web": None,
}

SUBSCRIPTION = {
    "data_inizio": "2026-01-01",
    "data_scadenza": "2027-01-01",
    "subscription_plans": {"ai_check": 5},
}


# ------------------------------------------------------------------- finti

class FakeQuery:
    def __init__(self, primary, table: str):
        self._primary = primary
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

    def upsert(self, payload, **kwargs):
        self._op, self._payload = "upsert", payload
        return self

    def eq(self, column, value):
        self.filters[column] = value
        return self

    def in_(self, column, values):
        self.filters[f"{column}__in"] = list(values)
        return self

    def gte(self, column, value):
        self.filters[f"{column}__gte"] = value
        return self

    def lt(self, column, value):
        self.filters[f"{column}__lt"] = value
        return self

    def order(self, *args, **kwargs):
        return self

    def range(self, *args):
        return self

    def limit(self, *args):
        return self

    async def execute(self):
        self._primary.ops.append((self._table, self._op, self._payload, dict(self.filters)))
        if self._op == "select":
            source = self._primary.selects.get(self._table, [])
            rows = source(self.filters) if callable(source) else source
            return SimpleNamespace(data=rows, count=len(rows))
        if self._op == "insert":
            if self._table in self._primary.insert_fail:
                from postgrest.exceptions import APIError as PgError

                raise PgError({
                    "message": "duplicate key value violates unique constraint",
                    "code": "23505", "hint": None, "details": None,
                })
            if self._table in self._primary.insert_fail_generic:
                from postgrest.exceptions import APIError as PgError

                raise PgError({"message": "boom", "code": "XX000", "hint": None, "details": None})
            return SimpleNamespace(
                data=[{
                    "id": f"gen-{self._table}-{len(self._primary.ops)}",
                    "created_at": "2026-07-07T12:00:00+00:00",
                    **(self._payload or {}),
                }],
                count=None,
            )
        if self._op == "upsert" and self._table in self._primary.upsert_fail:
            raise Exception("upsert non disponibile")
        if self._op == "update":
            if self._table in self._primary.update_returns_empty:
                return SimpleNamespace(data=[], count=None)
            return SimpleNamespace(data=[{**(self._payload or {})}], count=None)
        return SimpleNamespace(data=[], count=None)


class FakePrimary:
    def __init__(self, selects: dict | None = None, lock: bool = True):
        self.selects = selects or {}
        self.lock = lock
        self.ops: list = []
        self.rpcs: list = []
        self.insert_fail: set[str] = set()
        self.insert_fail_generic: set[str] = set()
        self.upsert_fail: set[str] = set()
        self.update_returns_empty: set[str] = set()

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

    def ops_for(self, table: str, op: str) -> list:
        return [(payload, filters) for t, o, payload, filters in self.ops if t == table and o == op]


def canned_extraction() -> ExtractionResult:
    return ExtractionResult.model_validate({
        "requisiti_obbligatori": [{
            "id": "R1", "testo": "Sede in Lombardia", "categoria": "territoriale",
            "dato_richiesto": "regione della sede",
            "citazione": {"sezione": "META", "testo_esatto": "Regioni ammesse (catalogo): Lombardia"},
        }],
        "criteri_valutazione": [{
            "id": "C1", "nome": "Innovazione", "categoria": "altro", "punti_max": None,
            "citazione": {"sezione": "S1", "testo_esatto": "innovazione"},
        }],
        "griglia": {"presente": False, "fonte": "assente", "punteggio_max_totale": None,
                    "soglia_minima": None, "note": None},
    })


def canned_matching() -> MatchingResult:
    return MatchingResult.model_validate({
        "requisiti": [{"id": "R1", "esito": "soddisfatto",
                       "dato_azienda": {"campo": "regione_nome", "valore": "Lazio"},
                       "motivazione": "ok"}],
        "criteri": [{"id": "C1", "esito": "soddisfatto",
                     "dato_azienda": {"campo": "ateco_codice", "valore": "62.01"},
                     "motivazione": "ok"}],
        "punti_di_forza": [], "punti_di_debolezza": [], "dati_mancanti": [],
    })


class FakeAi:
    def __init__(self, enabled=True, extract_error=None, match_error=None):
        self.enabled = enabled
        self.model = "claude-test"
        self.extract_calls: list[str] = []
        self.match_calls: list[str] = []
        self._extract_error = extract_error
        self._match_error = match_error

    async def extract(self, system, text):
        if self._extract_error:
            raise self._extract_error
        self.extract_calls.append(text)
        return canned_extraction(), AiUsage(input_tokens=4000, output_tokens=1500)

    async def match(self, system, text):
        if self._match_error:
            raise self._match_error
        self.match_calls.append(text)
        return canned_matching(), AiUsage(input_tokens=8000, output_tokens=2000)


# ------------------------------------------------------------------ fixture

@pytest.fixture(autouse=True)
def stub_settings(monkeypatch):
    for key, value in {
        "PRIMARY_SUPABASE_URL": "https://dummy.supabase.co",
        "PRIMARY_SUPABASE_SERVICE_ROLE_KEY": "k",
        "SECONDARY_SUPABASE_URL": "https://d2.supabase.co",
        "SECONDARY_SUPABASE_ANON_KEY": "k",
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

    monkeypatch.setattr("app.services.family_service.get_membership", membership)


@pytest.fixture(autouse=True)
def fake_bando(monkeypatch):
    bando = load_bando()

    async def fetch(secondary, slug):
        if slug != bando["slug"]:
            raise NotFoundError("Bando non trovato")
        return bando

    monkeypatch.setattr("app.services.bandi_service.fetch_bando_for_ai", fetch)
    return bando


@pytest.fixture
def spawned(monkeypatch):
    """Cattura le pipeline lanciate in background (senza eseguirle)."""
    captured: list = []
    monkeypatch.setattr(ai_check_service, "_spawn", captured.append)
    yield captured
    for coro in captured:
        coro.close()


def base_selects(**overrides) -> dict:
    selects = {
        "company_profiles": [COMPANY_ROW],
        "company_data": [],
        "company_people": [],
        "company_documents": [],
        "ai_checks": [],
        "user_subscriptions": [SUBSCRIPTION],
        "api_usage_events": [],
    }
    selects.update(overrides)
    return selects


SLUG = "lombardia-iniziativa-milo-nodi-interscambio"


# ----------------------------------------------------------------- richiesta

class TestRequestCheck:
    async def test_non_configurato(self, spawned):
        with pytest.raises(AiNotConfiguredError):
            await ai_check_service.request_check(
                FakePrimary(), None, FakeAi(enabled=False), USER, SLUG
            )

    async def test_figlio_attivo_bloccato(self, monkeypatch, spawned):
        async def membership(primary, user_id):
            return {"status": "active", "parent_id": "p0000000-0000-0000-0000-000000000009"}

        monkeypatch.setattr("app.services.family_service.get_membership", membership)
        with pytest.raises(ForbiddenError):
            await ai_check_service.request_check(
                FakePrimary(base_selects()), None, FakeAi(), USER, SLUG
            )

    async def test_senza_dati_aziendali(self, spawned):
        primary = FakePrimary(base_selects(company_profiles=[]))
        with pytest.raises(BadRequestError):
            await ai_check_service.request_check(primary, None, FakeAi(), USER, SLUG)

    async def test_dati_aziendali_insufficienti(self, spawned):
        spoglio = {**COMPANY_ROW, "ateco_codice": None, "ateco_id": None,
                   "settore_id": None, "regione_id": None}
        primary = FakePrimary(base_selects(company_profiles=[spoglio]))
        with pytest.raises(BadRequestError):
            await ai_check_service.request_check(primary, None, FakeAi(), USER, SLUG)

    async def test_bando_inesistente(self, spawned):
        with pytest.raises(NotFoundError):
            await ai_check_service.request_check(
                FakePrimary(base_selects()), None, FakeAi(), USER, "slug-inesistente"
            )

    async def test_cooldown(self, spawned):
        recent = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()

        def ai_checks(filters):
            if "bando_id" in filters:  # query del cooldown per coppia
                return [{"created_at": recent, "status": "ready"}]
            return []

        primary = FakePrimary(base_selects(ai_checks=ai_checks))
        with pytest.raises(AppError) as err:
            await ai_check_service.request_check(primary, None, FakeAi(), USER, SLUG)
        assert err.value.code == "ai_check_cooldown"

    async def test_analisi_pending_recente_da_409_non_cooldown(self, spawned):
        # Una POST che scavalca il flip pending→ready non deve bypassare
        # né il cooldown né l'indice unico: la pending recente dà 409.
        recent = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()

        def ai_checks(filters):
            if "bando_id" in filters:
                return [{"created_at": recent, "status": "pending"}]
            return []

        primary = FakePrimary(base_selects(ai_checks=ai_checks))
        with pytest.raises(AppError) as err:
            await ai_check_service.request_check(primary, None, FakeAi(), USER, SLUG)
        assert err.value.code == "ai_check_in_progress"

    async def test_quota_esaurita(self, spawned):
        # La quota conta le RIGHE pending+ready nella finestra (atomica,
        # indipendente dal registro consumi best-effort).
        def ai_checks(filters):
            if "status__in" in filters and "family_parent_id" in filters:
                return [{"id": "usata-1"}]
            return []

        primary = FakePrimary(base_selects(
            user_subscriptions=[{**SUBSCRIPTION, "subscription_plans": {"ai_check": 1}}],
            ai_checks=ai_checks,
        ))
        with pytest.raises(AiQuotaExceededError):
            await ai_check_service.request_check(primary, None, FakeAi(), USER, SLUG)
        # il lock è stato comunque rilasciato
        assert ("fn_release_import_lock", {"p_parent_id": OWNER}) in primary.rpcs

    async def test_piano_senza_ai_check(self, spawned):
        primary = FakePrimary(base_selects(
            user_subscriptions=[{**SUBSCRIPTION, "subscription_plans": {"ai_check": 0}}],
        ))
        with pytest.raises(AiQuotaExceededError) as err:
            await ai_check_service.request_check(primary, None, FakeAi(), USER, SLUG)
        assert "piano" in err.value.message

    async def test_lock_occupato(self, spawned):
        primary = FakePrimary(base_selects(), lock=False)
        with pytest.raises(AppError) as err:
            await ai_check_service.request_check(primary, None, FakeAi(), USER, SLUG)
        assert err.value.code == "ai_check_in_progress"

    async def test_doppia_analisi_respinta_dall_indice_unico(self, spawned):
        primary = FakePrimary(base_selects())
        primary.insert_fail.add("ai_checks")
        with pytest.raises(AppError) as err:
            await ai_check_service.request_check(primary, None, FakeAi(), USER, SLUG)
        assert err.value.code == "ai_check_in_progress"
        assert ("fn_release_import_lock", {"p_parent_id": OWNER}) in primary.rpcs

    async def test_happy_path(self, spawned, fake_bando):
        primary = FakePrimary(base_selects())
        out = await ai_check_service.request_check(primary, None, FakeAi(), USER, SLUG)

        assert out.status == "pending"
        assert out.bando_slug == SLUG
        assert out.bando_id == fake_bando["id"]

        [(inserted, _)] = primary.ops_for("ai_checks", "insert")
        assert inserted["family_parent_id"] == OWNER
        assert inserted["model"] == "claude-test"
        assert inserted["prompt_version"] == PROMPT_VERSION

        assert primary.ops_for("audit_log", "insert")
        assert len(spawned) == 1  # pipeline avviata in background
        assert ("fn_acquire_import_lock", {"p_parent_id": OWNER, "p_ttl_seconds": 30}) in primary.rpcs
        assert ("fn_release_import_lock", {"p_parent_id": OWNER}) in primary.rpcs

    async def test_guasto_db_generico_sull_insert_non_e_un_409(self, spawned):
        # Solo la violazione dell'indice unico (23505) è "analisi in corso":
        # qualunque altro guasto deve propagare con semantica upstream.
        from postgrest.exceptions import APIError as PgError

        primary = FakePrimary(base_selects())
        primary.insert_fail_generic.add("ai_checks")
        with pytest.raises(PgError):
            await ai_check_service.request_check(primary, None, FakeAi(), USER, SLUG)
        assert ("fn_release_import_lock", {"p_parent_id": OWNER}) in primary.rpcs

    async def test_failsafe_anche_sulla_post(self, spawned):
        primary = FakePrimary(base_selects())
        await ai_check_service.request_check(primary, None, FakeAi(), USER, SLUG)
        stale_updates = [
            (payload, filters)
            for payload, filters in primary.ops_for("ai_checks", "update")
            if "created_at__lt" in filters
        ]
        assert stale_updates and stale_updates[0][0]["status"] == "error"


# ----------------------------------------------------------------- pipeline

def pipeline_args(bando, primary, ai, check_id="check-1"):
    bando_text, sections = build_bando_input(bando, bando.get("contenuto"))
    return dict(
        check_id=check_id,
        user_id=USER["id"],
        owner_id=OWNER,
        bando=bando,
        bando_text=bando_text,
        sections=sections,
        content_hash="hash-attuale",
        company_pack="PACK",
        prechecks=facet_prechecks(bando, COMPANY_ROW, {}),
    )


class TestRunPipeline:
    async def test_happy_path_scrive_report_e_registro(self, fake_bando):
        primary = FakePrimary({"bando_requirements": []})
        ai = FakeAi()
        await ai_check_service._run_pipeline(primary, ai, **pipeline_args(fake_bando, primary, ai))

        assert len(ai.extract_calls) == 1 and len(ai.match_calls) == 1
        [(cache_row, _)] = primary.ops_for("bando_requirements", "upsert")
        assert cache_row["bando_id"] == fake_bando["id"]
        assert cache_row["content_hash"] == "hash-attuale"

        [(update, filters)] = primary.ops_for("ai_checks", "update")
        assert update["status"] == "ready"
        assert update["esito"] in ("ammissibile", "non_ammissibile", "da_verificare")
        assert update["tipo_punteggio"] == "euristico"
        assert update["report"]["schema_version"] == 1
        assert update["input_tokens"] == 12000 and update["output_tokens"] == 3500
        assert update["cost_cents"] == ai_check_service.cost_cents(12000, 3500)
        assert filters["status"] == "pending"  # update condizionato

        [(event, _)] = primary.ops_for("api_usage_events", "insert")
        assert event["provider"] == "anthropic"
        assert event["service"] == "ai_check"
        assert event["outcome"] == "success"
        assert event["request_meta"]["input_tokens"] == 12000

    async def test_cache_hit_salta_l_estrazione(self, fake_bando):
        cache = [{
            "extraction": canned_extraction().model_dump(),
            "content_hash": "hash-attuale",
            "prompt_version": PROMPT_VERSION,
        }]
        primary = FakePrimary({"bando_requirements": cache})
        ai = FakeAi()
        await ai_check_service._run_pipeline(primary, ai, **pipeline_args(fake_bando, primary, ai))

        assert not ai.extract_calls  # estrazione riusata
        assert len(ai.match_calls) == 1
        assert not primary.ops_for("bando_requirements", "upsert")
        [(update, _)] = primary.ops_for("ai_checks", "update")
        assert update["extraction_cached"] is True
        assert update["input_tokens"] == 8000  # solo lo stadio B

    async def test_hash_diverso_rigenera_l_estrazione(self, fake_bando):
        cache = [{
            "extraction": canned_extraction().model_dump(),
            "content_hash": "hash-vecchio",
            "prompt_version": PROMPT_VERSION,
        }]
        primary = FakePrimary({"bando_requirements": cache})
        ai = FakeAi()
        await ai_check_service._run_pipeline(primary, ai, **pipeline_args(fake_bando, primary, ai))
        assert len(ai.extract_calls) == 1
        assert primary.ops_for("bando_requirements", "upsert")

    async def test_timeout_marca_errore_e_registra_costo_stimato(self, fake_bando):
        primary = FakePrimary({"bando_requirements": []})
        ai = FakeAi(extract_error=AiTimeoutError())
        await ai_check_service._run_pipeline(primary, ai, **pipeline_args(fake_bando, primary, ai))

        [(update, filters)] = primary.ops_for("ai_checks", "update")
        assert update["status"] == "error"
        assert filters["status"] == "pending"
        [(event, _)] = primary.ops_for("api_usage_events", "insert")
        assert event["outcome"] == "timeout_unknown"
        assert event["cost_cents"] >= ai_check_service.TIMEOUT_COST_CENTS

    async def test_guasto_generico_non_solleva_e_registra_costo_speso(self, fake_bando):
        primary = FakePrimary({"bando_requirements": []})
        ai = FakeAi(match_error=RuntimeError("boom"))
        await ai_check_service._run_pipeline(primary, ai, **pipeline_args(fake_bando, primary, ai))

        [(update, _)] = primary.ops_for("ai_checks", "update")
        assert update["status"] == "error"
        [(event, _)] = primary.ops_for("api_usage_events", "insert")
        assert event["outcome"] == "error"
        # l'estrazione era già stata pagata: il costo va registrato
        assert event["cost_cents"] == ai_check_service.cost_cents(4000, 1500)

    async def test_cache_non_scrivibile_non_perde_l_analisi(self, fake_bando):
        # L'estrazione è già pagata e in memoria: un guasto sull'upsert della
        # cache non deve far fallire la pipeline né azzerare i token nel registro.
        primary = FakePrimary({"bando_requirements": []})
        primary.upsert_fail.add("bando_requirements")
        ai = FakeAi()
        await ai_check_service._run_pipeline(primary, ai, **pipeline_args(fake_bando, primary, ai))

        [(update, _)] = primary.ops_for("ai_checks", "update")
        assert update["status"] == "ready"
        [(event, _)] = primary.ops_for("api_usage_events", "insert")
        assert event["outcome"] == "success"
        assert event["request_meta"]["input_tokens"] == 12000

    async def test_riga_non_piu_pending_non_consuma_quota(self, fake_bando):
        primary = FakePrimary({"bando_requirements": []})
        primary.update_returns_empty.add("ai_checks")
        ai = FakeAi()
        await ai_check_service._run_pipeline(primary, ai, **pipeline_args(fake_bando, primary, ai))
        [(event, _)] = primary.ops_for("api_usage_events", "insert")
        assert event["outcome"] == "error"  # solo success consuma quota


# ------------------------------------------------------------------- lettura

CHECK_ID = "e0000000-0000-0000-0000-00000000c1ec"

READY_ROW = {
    "id": CHECK_ID, "company_profile_id": COMPANY_ID, "user_id": OWNER,
    "family_parent_id": OWNER, "bando_id": 17774, "bando_slug": SLUG,
    "bando_titolo": "Bando X", "status": "ready", "error_detail": None,
    "esito": "ammissibile", "punteggio": 85, "tipo_punteggio": "euristico",
    "report": {"schema_version": 1}, "model": "claude-test", "prompt_version": 1,
    "extraction_cached": False, "input_tokens": 1, "output_tokens": 1,
    "cost_cents": 8, "created_at": "2026-07-07T10:00:00+00:00",
    "updated_at": "2026-07-07T10:01:00+00:00", "ready_at": "2026-07-07T10:01:00+00:00",
}


class TestLettura:
    async def test_lista_globale_senza_report(self):
        primary = FakePrimary(base_selects(ai_checks=[READY_ROW]))
        resp = await ai_check_service.list_checks(primary, USER)
        assert resp.total == 1
        assert resp.items[0].report is None
        assert resp.quota.totale == 5

    async def test_lista_per_bando_include_report(self):
        primary = FakePrimary(base_selects(ai_checks=[READY_ROW]))
        resp = await ai_check_service.list_checks(primary, USER, bando_slug=SLUG)
        assert resp.items[0].report == {"schema_version": 1}

    async def test_failsafe_chiude_le_analisi_stale(self):
        primary = FakePrimary(base_selects())
        await ai_check_service.list_checks(primary, USER)
        [(update, filters)] = primary.ops_for("ai_checks", "update")
        assert update["status"] == "error"
        assert filters["status"] == "pending"
        assert "created_at__lt" in filters

    async def test_get_check(self):
        primary = FakePrimary(base_selects(ai_checks=[READY_ROW]))
        out = await ai_check_service.get_check(primary, USER, CHECK_ID)
        assert out.report == {"schema_version": 1}
        # il filtro di tenancy c'è DAVVERO: mai report di altre aziende
        [(_, filters)] = primary.ops_for("ai_checks", "select")
        assert filters["family_parent_id"] == OWNER
        assert filters["id"] == CHECK_ID

    async def test_get_check_non_trovato(self):
        primary = FakePrimary(base_selects())
        with pytest.raises(NotFoundError):
            await ai_check_service.get_check(
                primary, USER, "e0000000-0000-0000-0000-000000000bad"
            )

    async def test_get_check_id_malformato_e_un_404(self):
        # Un id non-UUID manderebbe PostgREST in 22P02 (→ 502): deve essere 404.
        primary = FakePrimary(base_selects())
        with pytest.raises(NotFoundError):
            await ai_check_service.get_check(primary, USER, "non-un-uuid")
        assert not primary.ops_for("ai_checks", "select")

    async def test_lista_applica_il_filtro_di_tenancy(self):
        primary = FakePrimary(base_selects(ai_checks=[READY_ROW]))
        await ai_check_service.list_checks(primary, USER)
        selects = primary.ops_for("ai_checks", "select")
        assert all(f["family_parent_id"] == OWNER for _, f in selects)

    async def test_bando_slug_vuoto_equivale_alla_lista_globale(self):
        primary = FakePrimary(base_selects(ai_checks=[READY_ROW]))
        resp = await ai_check_service.list_checks(primary, USER, bando_slug="  ")
        assert resp.items[0].report is None  # sintetica, come senza filtro

    async def test_quota_senza_abbonamento_attivo(self):
        primary = FakePrimary(base_selects(user_subscriptions=[]))
        quota = await ai_check_service.quota_for(primary, USER)
        assert quota.totale == 0 and quota.rimanenti == 0

    async def test_quota_finestra_e_conteggio_su_righe(self):
        # 2 righe (pending o ready) nella finestra → 2 usate su 5.
        primary = FakePrimary(base_selects(ai_checks=[{"id": "a"}, {"id": "b"}]))
        quota = await ai_check_service.quota_for(primary, USER)
        assert quota.usati == 2 and quota.rimanenti == 3
        [(_, filters)] = primary.ops_for("ai_checks", "select")
        assert filters["family_parent_id"] == OWNER
        assert filters["status__in"] == ["pending", "ready"]
        assert filters["created_at__gte"] == "2026-01-01"
        assert filters["created_at__lt"] == "2027-01-02"


def test_cost_cents():
    # 12k input × $3/MTok + 3.5k output × $15/MTok = 0.036 + 0.0525 $ → 9 cent
    assert ai_check_service.cost_cents(12000, 3500) == 9
    assert ai_check_service.cost_cents(0, 0) == 0
