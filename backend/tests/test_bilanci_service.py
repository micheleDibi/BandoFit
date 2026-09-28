"""Test del servizio bilanci (WP1): lettura con rimappatura pigra, recupero a
pagamento dello storico IT-advanced (guardie di spesa, esiti, lock, cooldown),
registrazione delle fonti.

Il primario finto è a STATO: tabelle in memoria con i filtri `eq` applicati
davvero e una RPC `fn_bilanci_registra_fonte` che replica la semantica della
0032 (upsert per anno e fonte, `sostituisci` per fonte, riga fusa ricalcolata
con `unisci_fonti`, il gemello Python verificato dal test DB). Così i test
seguono i dati attraverso più chiamate (import, rimappatura, lettura)."""

import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from postgrest.exceptions import APIError

from app.api.deps import ActiveCompany
from app.clients.openapi import OpenapiNessunDatoError, OpenapiNonInviataError
from app.core.errors import (
    AppError,
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    OpenapiNotConfiguredError,
    OpenapiTimeoutError,
    OpenapiUpstreamError,
    UpstreamError,
)
from app.services import bilanci_mapping, bilanci_service
from app.services.bilanci_mapping import CAMPI_BILANCIO, RigaFonte, unisci_fonti

FIXTURES = Path(__file__).parent / "fixtures" / "openapi"
OWNER = "a0000000-0000-0000-0000-000000000001"
COMPANY = "c0000000-0000-0000-0000-000000000001"
ALTRA_COMPANY = "c0000000-0000-0000-0000-000000000002"
PIVA = "09876543217"
PIVA_MASCHERATA = "098*****217"
LOCK_TOKEN = "70000000-0000-0000-0000-00000000beef"


def it_full_bilanci() -> dict:
    return json.loads((FIXTURES / "it_full_bilanci_sintetico.json").read_text())["data"]


def it_advanced_dato() -> dict:
    return json.loads((FIXTURES / "it_advanced_sintetico.json").read_text())["data"][0]


def _active(company_id: str | None = COMPANY, editable: bool = True) -> ActiveCompany:
    return ActiveCompany(company_id=company_id, owner_id=OWNER, editable=editable)


def _iso(minuti_fa: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minuti_fa)).isoformat()


# ------------------------------------------------------------------- finti

def _parse(value) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


class FakeQuery:
    def __init__(self, db: "FakeDB", table: str):
        self._db = db
        self._table = table
        self._op = "select"
        self._payload = None
        self._eq: dict = {}
        self._gt: dict = {}

    def select(self, *_args, **_kwargs):
        return self

    def insert(self, payload):
        self._op, self._payload = "insert", payload
        return self

    def upsert(self, payload, **_kwargs):
        self._op, self._payload = "upsert", payload
        return self

    def update(self, payload):
        self._op, self._payload = "update", payload
        return self

    def delete(self):
        self._op = "delete"
        return self

    def eq(self, column, value):
        self._eq[column] = str(value)
        return self

    def gt(self, column, value):
        self._gt[column] = value
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args):
        return self

    def _match(self, row: dict) -> bool:
        if any(str(row.get(c)) != v for c, v in self._eq.items()):
            return False
        return all(_parse(row[c]) > _parse(v) for c, v in self._gt.items() if row.get(c))

    async def execute(self):
        db = self._db
        db.ops.append((self._table, self._op, self._payload))
        if (self._table, self._op) in db.errors:
            raise db.errors[(self._table, self._op)]
        if self._table == "company_financials":
            db.tabelle["company_financials"] = db.righe_fuse()
        righe = db.tabelle.setdefault(self._table, [])
        if self._op == "select":
            return SimpleNamespace(data=[dict(r) for r in righe if self._match(r)])
        if self._op == "insert":
            nuove = self._payload if isinstance(self._payload, list) else [self._payload]
            righe.extend(dict(r) for r in nuove)
            return SimpleNamespace(data=[dict(r) for r in nuove])
        if self._op == "upsert":
            # merge-duplicates di PostgREST: si aggiornano SOLO le colonne passate
            chiave = db.chiavi_upsert[self._table]
            esistente = next((r for r in righe if r.get(chiave) == self._payload[chiave]), None)
            if esistente is None:
                righe.append({**db.default.get(self._table, {}), **self._payload})
            else:
                esistente.update(self._payload)
            return SimpleNamespace(data=[dict(self._payload)])
        if self._op == "delete":
            db.tabelle[self._table] = [r for r in righe if not self._match(r)]
        return SimpleNamespace(data=[])


class FakeDB:
    """Primario a stato con le RPC di 0032 che il servizio usa."""

    chiavi_upsert = {
        "company_financials_stato": "company_profile_id",
        "company_import_drafts": "parent_id",
        "company_data": "company_profile_id",
    }
    default = {"company_financials_stato": {"mapping_versione": 0, "advanced_fetch_count": 0}}

    def __init__(self, **tabelle):
        self.tabelle: dict[str, list[dict]] = {k: [dict(r) for r in v] for k, v in tabelle.items()}
        self.ops: list = []
        self.rpcs: list = []
        self.errors: dict = {}
        self.rpc_errors: dict = {}
        self.lock = True
        self.quota = True
        self.fonti: dict[tuple[str, int, str], RigaFonte] = {}

    # --- API PostgREST
    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self, name)

    def rpc(self, name: str, params: dict):
        self.rpcs.append((name, params))
        db = self

        class _Rpc:
            async def execute(self_inner):
                if name in db.rpc_errors:
                    raise db.rpc_errors[name]
                if name == "fn_acquire_import_lock_token":
                    return SimpleNamespace(data=LOCK_TOKEN if db.lock else None)
                if name == "fn_openapi_prenota_operazione":
                    return SimpleNamespace(data=db.quota)
                if name == "fn_bilanci_registra_fonte":
                    return SimpleNamespace(data=db.registra(params))
                return SimpleNamespace(data=None)

        return _Rpc()

    # --- simulazione di fn_bilanci_registra_fonte (semantica della 0032)
    def registra(self, p: dict) -> dict:
        azienda, fonte = p["p_company_id"], p["p_fonte"]
        righe = [
            RigaFonte(
                anno=r["anno"],
                data_chiusura=date.fromisoformat(r["data_chiusura"]) if r["data_chiusura"] else None,
                tipo_bilancio=r["tipo_bilancio"],
                ruolo=r["ruolo"],
                valori={k: Decimal(v) for k, v in r["valori"].items()},
            )
            for r in p["p_righe"]
        ]
        if p["p_sostituisci"]:
            nuovi = {r.anno for r in righe}
            for chiave in [k for k in self.fonti if k[0] == azienda and k[2] == fonte]:
                if chiave[1] not in nuovi:
                    del self.fonti[chiave]
        for riga in righe:
            attuale = self.fonti.get((azienda, riga.anno, fonte))
            if attuale is not None and attuale.ruolo == "corrente" and riga.ruolo == "comparativo":
                continue
            self.fonti[(azienda, riga.anno, fonte)] = riga
        return {"anni": sorted({r.anno for r in righe})}

    def righe_fuse(self) -> list[dict]:
        per_anno: dict[tuple[str, int], dict[str, RigaFonte]] = {}
        for (azienda, anno, fonte), riga in self.fonti.items():
            per_anno.setdefault((azienda, anno), {})[fonte] = riga
        righe = []
        for (azienda, anno), fonti in sorted(per_anno.items()):
            valori, fonte_per_campo = unisci_fonti(fonti)
            chiusure = [r.data_chiusura for r in fonti.values() if r.data_chiusura]
            righe.append({
                "company_profile_id": azienda,
                "anno": anno,
                "data_chiusura": chiusure[0].isoformat() if chiusure else None,
                "tipo_bilancio": "ignoto",
                "fonte_per_campo": fonte_per_campo,
                # PostgREST restituisce i numeric come numeri JSON
                **{c: (float(v) if v is not None else None) for c, v in valori.items()},
            })
        return righe

    # --- ispezione
    def stato(self, company_id: str = COMPANY) -> dict | None:
        return next(
            (r for r in self.tabelle.get("company_financials_stato", [])
             if r["company_profile_id"] == company_id),
            None,
        )

    def ops_for(self, table: str, op: str) -> list:
        return [payload for t, o, payload in self.ops if t == table and o == op]

    def rpc_names(self) -> list[str]:
        return [name for name, _ in self.rpcs]

    def eventi(self) -> list[dict]:
        return self.tabelle.get("api_usage_events", [])


def company_row(**over) -> dict:
    return {"id": COMPANY, "parent_id": OWNER, "ragione_sociale": "ALFA", "partita_iva": PIVA,
            **over}


def company_data(raw: dict | None = None, **over) -> dict:
    return {
        "company_profile_id": COMPANY,
        "raw": it_full_bilanci() if raw is None else raw,
        "derived": {},
        "piva_fetched": PIVA,
        "sandbox": False,
        "fetch_count": 1,
        "fetched_at": _iso(60 * 24),
        **over,
    }


def draft(company_profile_id: str | None = COMPANY, tentato_minuti_fa: float = 1) -> dict:
    return {
        "parent_id": OWNER,
        "partita_iva": PIVA,
        "raw": it_full_bilanci(),
        "sandbox": False,
        "fetched_at": _iso(tentato_minuti_fa),
        "expires_at": _iso(-20),
        "company_profile_id": company_profile_id,
        "advanced_raw": None,
        "advanced_esito": "timeout",
        "advanced_motivo": "esito_incerto",
        "advanced_tentato_at": _iso(tentato_minuti_fa),
    }


def fake_openapi(advanced=None, errore: Exception | None = None, sandbox=False, enabled=True,
                 prima_della_chiamata=None, attesa: float = 0, attesa_token: float = 0):
    """`calls` = chiamate A PAGAMENTO a IT-advanced con (P.IVA, tetto di
    tempo); `tokens` = mint (gratuiti) chiesti prima della chiamata."""
    calls: list = []
    tokens: list = []

    async def prepara_token(gruppo):
        tokens.append(gruppo)
        if attesa_token:
            await asyncio.sleep(attesa_token)

    async def it_advanced(piva, *, timeout_s):
        if prima_della_chiamata:
            prima_della_chiamata()
        calls.append((piva, round(timeout_s)))
        if attesa:
            await asyncio.sleep(attesa)
        if errore:
            raise errore
        return it_advanced_dato() if advanced is None else advanced

    return SimpleNamespace(
        enabled=enabled, sandbox=sandbox, it_advanced=it_advanced, prepara_token=prepara_token,
        calls=calls, tokens=tokens,
    )


RILASCIO = ("fn_release_import_lock_token", {"p_parent_id": OWNER, "p_token": LOCK_TOKEN})


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


def db_base(**extra) -> FakeDB:
    tabelle = {"company_profiles": [company_row()], "company_data": [company_data()]}
    tabelle.update(extra)
    return FakeDB(**tabelle)


# ---------------------------------------------------------------- lettura

class TestLettura:
    async def test_senza_azienda_mai_richiesti(self):
        out = await bilanci_service.get_bilanci(FakeDB(), _active(company_id=None))
        assert out.stato == "mai_richiesti" and out.esercizi == [] and out.fasce is None

    async def test_backfill_gratuito_delle_aziende_gia_importate(self):
        """Import precedente a WP1: nessuno stato, company_data.raw presente.
        La prima lettura registra gratis l'esercizio di IT-full."""
        db = db_base()
        out = await bilanci_service.get_bilanci(db, _active())
        assert "fn_openapi_prenota_operazione" not in db.rpc_names()  # nulla di pagato
        [(nome, params)] = [(n, p) for n, p in db.rpcs if n == "fn_bilanci_registra_fonte"]
        assert params["p_fonte"] == "it_full" and params["p_riferimento"] == "rimappatura"
        assert params["p_sostituisci"] is False
        assert db.stato()["mapping_versione"] == bilanci_mapping.MAPPING_BILANCI_VERSIONE
        # esercizio fuso con la fonte di ogni campo
        [esercizio] = out.esercizi
        assert esercizio.anno == 2021 and esercizio.data_chiusura == "2021-12-31"
        assert esercizio.patrimonio_netto == 563473.0
        assert esercizio.fonti["patrimonio_netto"] == "it_full"
        assert "debiti_totali" not in esercizio.fonti  # solo i campi valorizzati
        # lo storico non è mai stato chiesto: CTA «Recupera»
        assert out.stato == "disponibili"
        assert out.storico_esito is None and out.motivo == "non_richiesto"
        # una seconda lettura non rimappa più
        prima = len(db.rpcs)
        await bilanci_service.get_bilanci(db, _active())
        assert len(db.rpcs) == prima

    async def test_rimappatura_it_full_non_sostituisce_gli_anni_precedenti(self, monkeypatch):
        """Due import di anni diversi, poi una correzione del mapping: il raw
        di IT-full conserva solo l'ultimo esercizio, ma il patrimonio netto
        dell'anno vecchio NON deve sparire (è l'unica fonte pluriennale del PN)."""
        db = db_base(company_data=[])
        vecchio = it_full_bilanci()
        vecchio["ecofin"].update(
            {"balanceSheetDate": "2020-12-31T00:00:00", "turnoverYear": 2020,
             "turnover": 3712554.0, "netWorth": 401000.0}
        )
        # import 1 (esercizio 2020) e import 2 (esercizio 2021)
        await bilanci_service.persisti_import(
            db, COMPANY, piva=PIVA, payload=vecchio, advanced=None, sandbox=False
        )
        await bilanci_service.persisti_import(
            db, COMPANY, piva=PIVA, payload=it_full_bilanci(), advanced=None, sandbox=False
        )
        # company_data.raw conserva solo l'ultimo (upsert per azienda)
        db.tabelle["company_data"] = [company_data()]
        # correzione del mapping: la versione sale, la lettura rimappa
        monkeypatch.setattr(bilanci_service, "MAPPING_BILANCI_VERSIONE", 2)
        out = await bilanci_service.get_bilanci(db, _active())
        rimappature = [p for n, p in db.rpcs if n == "fn_bilanci_registra_fonte"
                       and p["p_riferimento"] == "rimappatura"]
        assert [(p["p_fonte"], p["p_sostituisci"]) for p in rimappature] == [("it_full", False)]
        assert [e.anno for e in out.esercizi] == [2020, 2021]
        assert out.esercizi[0].patrimonio_netto == 401000.0  # l'anno vecchio resta
        assert db.stato()["mapping_versione"] == 2

    async def test_rimappatura_storico_da_advanced_raw(self, monkeypatch):
        db = db_base(company_financials_stato=[{
            "company_profile_id": COMPANY, "advanced_esito": "ok", "advanced_motivo": None,
            "advanced_tentato_at": _iso(600), "advanced_fetched_at": _iso(600),
            "advanced_raw": it_advanced_dato(), "advanced_sandbox": False,
            "advanced_fetch_count": 1, "mapping_versione": 0,
        }])
        out = await bilanci_service.get_bilanci(db, _active())
        fonti = {p["p_fonte"]: p for n, p in db.rpcs if n == "fn_bilanci_registra_fonte"}
        assert fonti["it_advanced"]["p_sostituisci"] is True  # lo storico sì
        assert fonti["it_full"]["p_sostituisci"] is False
        assert [e.anno for e in out.esercizi] == [2017, 2018, 2019, 2020, 2021, 2022]
        # 2021: patrimonio netto da IT-full, utile da IT-full (rango superiore)
        e2021 = next(e for e in out.esercizi if e.anno == 2021)
        assert e2021.fonti["patrimonio_netto"] == "it_full"
        assert e2021.fonti["risultato_esercizio"] == "it_full"
        # 2019: solo storico, e netWorth di IT-advanced è l'UTILE
        e2019 = next(e for e in out.esercizi if e.anno == 2019)
        assert e2019.risultato_esercizio == 201877.0 and e2019.patrimonio_netto is None
        assert out.storico_esito == "ok" and out.motivo is None
        # indicatori e fasce calcolati sugli esercizi
        chiavi = [i.chiave for i in out.indicatori]
        assert chiavi[0] == "crescita_fatturato_pct"
        crescita = out.indicatori[0]
        assert crescita.anni == [2021, 2022] and crescita.valore == pytest.approx(15.1, abs=0.01)
        assert out.fasce.fatturato == "2m_10m" and out.fasce.anno_riferimento == 2022

    async def test_rimappatura_fallita_non_rompe_la_lettura(self):
        db = db_base()
        db.rpc_errors["fn_bilanci_registra_fonte"] = APIError(
            {"message": "x", "code": "P0001", "hint": None, "details": "righe_non_valide"}
        )
        out = await bilanci_service.get_bilanci(db, _active())
        assert out.stato == "mai_richiesti"
        assert db.stato() is None  # versione non aggiornata: si riproverà

    async def test_non_disponibili_con_motivo_e_cooldown(self):
        db = db_base(company_data=[], company_financials_stato=[{
            "company_profile_id": COMPANY, "advanced_esito": "non_disponibili",
            "advanced_motivo": "nessun_bilancio", "advanced_tentato_at": _iso(2),
            "advanced_sandbox": False, "mapping_versione": 1,
        }])
        out = await bilanci_service.get_bilanci(db, _active())
        assert (out.stato, out.motivo, out.storico_esito) == (
            "non_disponibili", "nessun_bilancio", "non_disponibili"
        )
        assert out.ultimo_tentativo_at is not None
        fine = _parse(out.recuperabile_da)
        assert timedelta(minutes=7) < fine - datetime.now(timezone.utc) < timedelta(minutes=9)

    async def test_membro_legge_senza_cooldown_e_senza_draft(self):
        db = db_base(company_import_drafts=[draft()])
        out = await bilanci_service.get_bilanci(db, _active(editable=False))
        assert out.editable is False and out.recuperabile_da is None
        assert db.ops_for("company_import_drafts", "select") == []


# --------------------------------------------------------------- recupero

class TestRecuperaGuardie:
    async def test_non_configurato_503(self):
        with pytest.raises(OpenapiNotConfiguredError):
            await bilanci_service.recupera_bilanci(db_base(), fake_openapi(enabled=False), _active())

    async def test_membro_403(self):
        openapi = fake_openapi()
        with pytest.raises(ForbiddenError):
            await bilanci_service.recupera_bilanci(db_base(), openapi, _active(editable=False))
        assert openapi.calls == []

    async def test_senza_azienda_404(self):
        with pytest.raises(NotFoundError):
            await bilanci_service.recupera_bilanci(db_base(), fake_openapi(), _active(None))

    async def test_senza_import_400_anche_con_piva_nel_profilo(self):
        """La P.IVA del profilo si cambia liberamente (PUT /me/company): usarla
        darebbe lo storico ESATTO di qualsiasi impresa, senza che la P.IVA
        resti mai legata all'azienda. Si recupera solo per quella importata."""
        db = FakeDB(company_profiles=[company_row()])
        openapi = fake_openapi()
        with pytest.raises(BadRequestError) as exc:
            await bilanci_service.recupera_bilanci(db, openapi, _active())
        assert "importa prima" in exc.value.message
        assert openapi.calls == [] and openapi.tokens == [] and db.rpcs == []

    async def test_piva_del_profilo_diversa_da_quella_importata_400(self):
        # Come `piva_diversa` nell'anteprima: lo storico non si paga per una
        # P.IVA che l'azienda non dichiara.
        db = db_base(company_profiles=[company_row(partita_iva="00000000000")])
        openapi = fake_openapi()
        with pytest.raises(BadRequestError) as exc:
            await bilanci_service.recupera_bilanci(db, openapi, _active())
        assert "correggila nei dati aziendali" in exc.value.message
        assert openapi.calls == [] and db.rpcs == []

    async def test_piva_importata_anche_col_profilo_vuoto(self):
        db = db_base(company_profiles=[company_row(partita_iva=None)])
        openapi = fake_openapi()
        await bilanci_service.recupera_bilanci(db, openapi, _active())
        assert openapi.calls == [(PIVA, 25)]

    async def test_societa_di_persone_409(self):
        raw = it_full_bilanci()
        raw["legalForm"]["legalForm"]["code"] = "SP"
        db = db_base(company_data=[company_data(raw)])
        openapi = fake_openapi()
        with pytest.raises(AppError) as exc:
            await bilanci_service.recupera_bilanci(db, openapi, _active())
        assert (exc.value.status_code, exc.value.code) == (409, "bilanci_non_previsti")
        assert openapi.calls == [] and db.rpcs == []

    async def test_cooldown_dallo_stato(self):
        db = db_base(company_financials_stato=[{
            "company_profile_id": COMPANY, "advanced_tentato_at": _iso(3), "mapping_versione": 1,
        }])
        openapi = fake_openapi()
        with pytest.raises(AppError) as exc:
            await bilanci_service.recupera_bilanci(db, openapi, _active())
        assert exc.value.code == "bilanci_cooldown"
        assert "7 minuti" in exc.value.message  # 10 − 3, arrotondato per eccesso
        assert openapi.calls == [] and db.rpcs == []

    async def test_cooldown_ultimo_minuto_al_singolare(self):
        db = db_base(company_financials_stato=[{
            "company_profile_id": COMPANY, "advanced_tentato_at": _iso(9.5), "mapping_versione": 1,
        }])
        with pytest.raises(AppError) as exc:
            await bilanci_service.recupera_bilanci(db, fake_openapi(), _active())
        assert exc.value.message.endswith("riprova tra circa un minuto")

    async def test_cooldown_dal_draft_della_stessa_azienda(self):
        # IT-advanced appena tentato nell'anteprima (non ancora confermata)
        db = db_base(company_import_drafts=[draft(COMPANY, tentato_minuti_fa=2)])
        openapi = fake_openapi()
        with pytest.raises(AppError) as exc:
            await bilanci_service.recupera_bilanci(db, openapi, _active())
        assert exc.value.code == "bilanci_cooldown"
        assert openapi.calls == []

    async def test_draft_di_unaltra_azienda_non_da_cooldown(self):
        db = db_base(company_import_drafts=[draft(ALTRA_COMPANY, tentato_minuti_fa=2)])
        openapi = fake_openapi()
        await bilanci_service.recupera_bilanci(db, openapi, _active())
        assert len(openapi.calls) == 1

    async def test_cooldown_scaduto_procede(self):
        db = db_base(company_financials_stato=[{
            "company_profile_id": COMPANY, "advanced_tentato_at": _iso(11), "mapping_versione": 1,
        }])
        openapi = fake_openapi()
        await bilanci_service.recupera_bilanci(db, openapi, _active())
        assert len(openapi.calls) == 1

    async def test_lock_occupato_409(self):
        db = db_base()
        db.lock = False
        openapi = fake_openapi()
        with pytest.raises(AppError) as exc:
            await bilanci_service.recupera_bilanci(db, openapi, _active())
        assert exc.value.code == "import_in_progress"
        assert openapi.calls == []
        assert "fn_openapi_prenota_operazione" not in db.rpc_names()
        assert (
            "fn_acquire_import_lock_token",
            {"p_parent_id": OWNER, "p_ttl_seconds": bilanci_service.BILANCI_LOCK_TTL_SECONDS},
        ) in db.rpcs

    async def test_quota_esaurita_429(self):
        db = db_base()
        db.quota = False
        openapi = fake_openapi()
        with pytest.raises(AppError) as exc:
            await bilanci_service.recupera_bilanci(db, openapi, _active())
        assert (exc.value.status_code, exc.value.code) == (429, "limite_giornaliero_openapi")
        assert openapi.calls == []
        assert db.stato() is None  # nessun tentativo annotato: non si è speso
        assert RILASCIO in db.rpcs

    async def test_quota_non_verificabile_fail_closed(self):
        db = db_base()
        db.rpc_errors["fn_openapi_prenota_operazione"] = APIError(
            {"message": "boom", "code": "XX000", "hint": None, "details": None}
        )
        openapi = fake_openapi()
        with pytest.raises(UpstreamError):
            await bilanci_service.recupera_bilanci(db, openapi, _active())
        assert openapi.calls == []
        assert RILASCIO in db.rpcs

    async def test_tentativo_non_annotabile_nessuna_chiamata(self):
        db = db_base()
        db.errors[("company_financials_stato", "upsert")] = RuntimeError("db giù")
        openapi = fake_openapi()
        with pytest.raises(UpstreamError):
            await bilanci_service.recupera_bilanci(db, openapi, _active())
        assert openapi.calls == []
        assert RILASCIO in db.rpcs


class TestRecuperaEsiti:
    async def test_ok_200(self):
        db = db_base()
        visto_prima: dict = {}

        def controlla_tentato():
            # il tentativo è annotato PRIMA della chiamata a pagamento
            visto_prima["stato"] = dict(db.stato() or {})

        openapi = fake_openapi(prima_della_chiamata=controlla_tentato)
        out = await bilanci_service.recupera_bilanci(db, openapi, _active())

        assert visto_prima["stato"]["advanced_tentato_at"]
        assert openapi.calls == [(PIVA, 25)]
        # stato: esito, raw (mai al client), contatore, P.IVA usata
        stato = db.stato()
        assert stato["advanced_esito"] == "ok" and stato["advanced_raw"] == it_advanced_dato()
        assert stato["advanced_fetch_count"] == 1
        assert stato["advanced_tentato_at"] == visto_prima["stato"]["advanced_tentato_at"]
        # storico registrato sostituendo il precedente
        [reg] = [p for n, p in db.rpcs
                 if n == "fn_bilanci_registra_fonte" and p["p_riferimento"] == "recupero"]
        assert reg["p_fonte"] == "it_advanced" and reg["p_sostituisci"] is True
        # registro consumi: IT-advanced a costo pieno, P.IVA mascherata
        [evento] = db.eventi()
        assert evento["service"] == "IT-advanced" and evento["outcome"] == "success"
        assert evento["cost_cents"] == 10
        assert evento["request_meta"]["piva"] == PIVA_MASCHERATA
        # audit senza P.IVA
        [audit] = db.tabelle["audit_log"]
        assert audit["action"] == "company.bilanci_recuperati"
        assert audit["payload"]["esito"] == "ok"
        assert audit["payload"]["anni"] == [2017, 2018, 2019, 2020, 2021, 2022]
        assert PIVA not in json.dumps(audit)
        assert RILASCIO in db.rpcs
        # risposta: lo storico unito all'esercizio di IT-full (backfill)
        assert out.stato == "disponibili" and out.storico_esito == "ok"
        assert [e.anno for e in out.esercizi] == [2017, 2018, 2019, 2020, 2021, 2022]
        assert "advanced_raw" not in out.model_dump_json()
        assert out.recuperabile_da is not None  # cooldown appena ripartito

    async def test_nessun_bilancio_200(self):
        db = db_base()
        openapi = fake_openapi(errore=OpenapiNessunDatoError(404))
        out = await bilanci_service.recupera_bilanci(db, openapi, _active())
        assert out.storico_esito == "non_disponibili"
        assert db.stato()["advanced_motivo"] == "nessun_bilancio"
        assert db.eventi()[0]["cost_cents"] == 0

    async def test_errore_provider_502(self):
        db = db_base()
        openapi = fake_openapi(errore=OpenapiNonInviataError())
        with pytest.raises(OpenapiUpstreamError) as exc:
            await bilanci_service.recupera_bilanci(db, openapi, _active())
        assert exc.value.status_code == 502
        assert (db.stato()["advanced_esito"], db.stato()["advanced_motivo"]) == (
            "errore", "errore_provider"
        )
        assert (db.eventi()[0]["outcome"], db.eventi()[0]["cost_cents"]) == ("error", 0)
        assert RILASCIO in db.rpcs  # nessun addebito: il lock si rilascia

    async def test_mismatch_502_e_dati_scartati(self):
        dato = it_advanced_dato()
        dato["vatCode"] = dato["taxCode"] = "00000000000"
        db = db_base()
        with pytest.raises(OpenapiUpstreamError) as exc:
            await bilanci_service.recupera_bilanci(db, fake_openapi(advanced=dato), _active())
        assert db.stato()["advanced_esito"] == "mismatch"
        assert "advanced_raw" not in db.stato()
        assert "provider" not in exc.value.message  # italiano piano
        assert "fn_bilanci_registra_fonte" not in [
            n for n, p in db.rpcs if p.get("p_riferimento") == "recupero"
        ]

    async def test_timeout_504_lock_lasciato_scadere(self):
        db = db_base()
        openapi = fake_openapi(errore=OpenapiTimeoutError())
        with pytest.raises(OpenapiTimeoutError) as exc:
            await bilanci_service.recupera_bilanci(db, openapi, _active())
        assert exc.value.status_code == 504
        [evento] = db.eventi()
        assert (evento["outcome"], evento["cost_cents"]) == ("timeout_unknown", 10)
        assert (db.stato()["advanced_esito"], db.stato()["advanced_motivo"]) == (
            "timeout", "esito_incerto"
        )
        # esito ignoto: il lock NON si rilascia (niente doppio addebito al buio)
        assert not any(n.startswith("fn_release") for n in db.rpc_names())
        assert len(openapi.calls) == 1  # mai retry

    async def test_tetto_complessivo_della_chiamata(self, monkeypatch):
        # mint lento + GET: oltre BILANCI_DEADLINE_SECONDS l'esito è ignoto
        monkeypatch.setattr(bilanci_service, "BILANCI_DEADLINE_SECONDS", 0.01)
        db = db_base()
        openapi = fake_openapi(attesa=0.5)
        with pytest.raises(OpenapiTimeoutError):
            await bilanci_service.recupera_bilanci(db, openapi, _active())
        assert [(e["outcome"], e["cost_cents"]) for e in db.eventi()] == [("timeout_unknown", 10)]
        assert db.stato()["advanced_esito"] == "timeout"
        assert db.stato()["advanced_tentato_at"]

    async def test_registrazione_fallita_versione_azzerata(self):
        db = db_base(company_financials_stato=[{
            "company_profile_id": COMPANY, "advanced_tentato_at": _iso(30), "mapping_versione": 1,
        }])
        db.rpc_errors["fn_bilanci_registra_fonte"] = APIError(
            {"message": "x", "code": "P0001", "hint": None, "details": "azienda_non_trovata"}
        )
        out = await bilanci_service.recupera_bilanci(db, fake_openapi(), _active())
        # il raw pagato è al sicuro; la versione torna a 0 per la rimappatura pigra
        assert db.stato()["advanced_raw"] == it_advanced_dato()
        assert db.stato()["mapping_versione"] == 0
        assert out.storico_esito == "ok"


class TestStoricoGiaRecuperato:
    """Un tentativo successivo non riuscito (o saltato) non rende «incompleto»
    uno storico già salvato per la stessa P.IVA: la UI spingerebbe a
    ripagarlo. Uno storico di un'ALTRA P.IVA invece non resta mai."""

    @staticmethod
    def _stato_ok(piva: str = PIVA) -> dict:
        return {
            "company_profile_id": COMPANY, "advanced_esito": "ok", "advanced_motivo": None,
            "advanced_tentato_at": _iso(60 * 24), "advanced_fetched_at": _iso(60 * 24),
            "advanced_raw": it_advanced_dato(), "advanced_sandbox": False,
            "advanced_fetch_count": 1, "advanced_piva": piva, "mapping_versione": 1,
        }

    def _db_con_storico(self, piva: str = PIVA) -> FakeDB:
        db = db_base(company_financials_stato=[self._stato_ok(piva)])
        for riga in bilanci_mapping.da_it_advanced(it_advanced_dato()):
            db.fonti[(COMPANY, riga.anno, "it_advanced")] = riga
        return db

    @pytest.mark.parametrize(
        "advanced",
        [
            bilanci_service.EsitoAdvanced.saltato("tempo_insufficiente"),
            bilanci_service.EsitoAdvanced("errore", "errore_provider", tentato_at=_iso(0)),
            bilanci_service.EsitoAdvanced("timeout", "esito_incerto", tentato_at=_iso(0)),
        ],
        ids=["saltato", "errore", "timeout"],
    )
    async def test_reimport_non_declassa_lo_storico(self, advanced):
        db = self._db_con_storico()
        await bilanci_service.persisti_import(
            db, COMPANY, piva=PIVA, payload=it_full_bilanci(), advanced=advanced, sandbox=False
        )
        stato = db.stato()
        assert (stato["advanced_esito"], stato["advanced_motivo"]) == ("ok", None)
        assert stato["advanced_raw"] == it_advanced_dato()
        if advanced.tentato_at:  # il cooldown riparte comunque
            assert stato["advanced_tentato_at"] == advanced.tentato_at
        out = await bilanci_service.get_bilanci(db, _active())
        assert out.storico_esito == "ok" and out.motivo is None
        assert [e.anno for e in out.esercizi] == [2017, 2018, 2019, 2020, 2021, 2022]

    async def test_recupero_fallito_non_declassa_lo_storico(self):
        db = self._db_con_storico()
        db.tabelle["company_financials_stato"][0]["advanced_tentato_at"] = _iso(30)
        with pytest.raises(OpenapiUpstreamError):
            await bilanci_service.recupera_bilanci(
                db, fake_openapi(errore=OpenapiNonInviataError()), _active()
            )
        assert db.stato()["advanced_esito"] == "ok"
        assert _parse(db.stato()["advanced_tentato_at"]) > datetime.now(timezone.utc) - timedelta(
            minutes=1
        )

    async def test_storico_di_unaltra_piva_scartato(self):
        """Storico salvato per un'altra impresa (P.IVA diversa): al primo
        tentativo per questa P.IVA, anche fallito, via righe e raw."""
        db = self._db_con_storico(piva="00000000000")
        await bilanci_service.persisti_import(
            db, COMPANY, piva=PIVA, payload=it_full_bilanci(),
            advanced=bilanci_service.EsitoAdvanced.saltato("tempo_insufficiente"), sandbox=False,
        )
        assert not [k for k in db.fonti if k[2] == "it_advanced"]
        stato = db.stato()
        assert stato["advanced_raw"] is None and stato["advanced_piva"] is None
        assert stato["advanced_fetched_at"] is None
        assert (stato["advanced_esito"], stato["advanced_motivo"]) == (
            "saltato", "tempo_insufficiente"
        )
        out = await bilanci_service.get_bilanci(db, _active())
        assert [e.anno for e in out.esercizi] == [2021]  # solo l'esercizio di IT-full

    async def test_rimappatura_ignora_lo_storico_di_unaltra_piva(self):
        db = db_base(company_financials_stato=[
            {**self._stato_ok(piva="00000000000"), "mapping_versione": 0}
        ])
        out = await bilanci_service.get_bilanci(db, _active())
        fonti = [p["p_fonte"] for n, p in db.rpcs if n == "fn_bilanci_registra_fonte"]
        assert fonti == ["it_full"]
        assert [e.anno for e in out.esercizi] == [2021]

    async def test_advanced_piva_solo_con_esito_ok(self):
        db = db_base()
        with pytest.raises(OpenapiUpstreamError):
            await bilanci_service.recupera_bilanci(
                db, fake_openapi(errore=OpenapiNonInviataError()), _active()
            )
        assert "advanced_piva" not in db.stato()
        db.tabelle["company_financials_stato"][0]["advanced_tentato_at"] = _iso(30)
        await bilanci_service.recupera_bilanci(db, fake_openapi(), _active())
        assert db.stato()["advanced_piva"] == PIVA


class TestChiamaItAdvanced:
    async def test_mint_lento_non_e_un_esito_incerto(self):
        """Il mint del token (primo uso dopo un riavvio) esaurisce il tetto:
        la richiesta a pagamento non è mai partita → errore a costo 0, non
        un «esito incerto» con costo pieno e lock lasciato scadere."""
        db = FakeDB()
        openapi = fake_openapi(attesa_token=0.2)
        esito = await bilanci_service.chiama_it_advanced(
            db, openapi, owner_id=OWNER, piva=PIVA, timeout_s=0.05
        )
        assert (esito.esito, esito.motivo) == ("errore", "errore_provider")
        assert openapi.tokens == ["advanced"] and openapi.calls == []
        assert [(e["outcome"], e["cost_cents"]) for e in db.eventi()] == [("error", 0)]

    async def test_il_mint_non_consuma_il_tetto_della_chiamata(self):
        db = FakeDB()
        openapi = fake_openapi()
        esito = await bilanci_service.chiama_it_advanced(
            db, openapi, owner_id=OWNER, piva=PIVA, timeout_s=25
        )
        assert esito.esito == "ok"
        assert openapi.tokens == ["advanced"] and openapi.calls == [(PIVA, 25)]

    @pytest.mark.parametrize(("status", "outcome", "costo"),
                             [(200, "success", 10), (204, "success", 10), (404, "error", 0)])
    async def test_nessun_dato_costo_per_status(self, status, outcome, costo):
        # 200 con `data` vuota è una chiamata riuscita come il 204: addebitabile.
        db = FakeDB()
        esito = await bilanci_service.chiama_it_advanced(
            db, fake_openapi(errore=OpenapiNessunDatoError(status)), owner_id=OWNER, piva=PIVA,
            timeout_s=25,
        )
        assert (esito.esito, esito.motivo) == ("non_disponibili", "nessun_bilancio")
        assert (db.eventi()[0]["outcome"], db.eventi()[0]["cost_cents"]) == (outcome, costo)

    async def test_sandbox_costo_zero(self):
        db = FakeDB()
        esito = await bilanci_service.chiama_it_advanced(
            db, fake_openapi(sandbox=True), owner_id=OWNER, piva=PIVA, timeout_s=25
        )
        assert esito.esito == "ok" and esito.tentato_at
        assert db.eventi()[0]["cost_cents"] == 0

    async def test_204_costo_prudente(self):
        db = FakeDB()
        esito = await bilanci_service.chiama_it_advanced(
            db, fake_openapi(errore=OpenapiNessunDatoError(204)), owner_id=OWNER, piva=PIVA,
            timeout_s=25,
        )
        assert (esito.esito, esito.motivo) == ("non_disponibili", "nessun_bilancio")
        assert (db.eventi()[0]["outcome"], db.eventi()[0]["cost_cents"]) == ("success", 10)

    async def test_errore_inatteso_non_solleva(self):
        db = FakeDB()
        esito = await bilanci_service.chiama_it_advanced(
            db, fake_openapi(errore=RuntimeError("bug")), owner_id=OWNER, piva=PIVA, timeout_s=25
        )
        assert (esito.esito, esito.motivo, esito.raw) == ("errore", "errore_provider", None)


class TestRegistraFonte:
    async def test_payload_della_rpc(self):
        db = FakeDB()
        righe = bilanci_mapping.da_it_full(it_full_bilanci())
        anni = await bilanci_service.registra_fonte(db, COMPANY, "it_full", righe, "import")
        assert anni == [2021]
        [(nome, params)] = db.rpcs
        assert nome == "fn_bilanci_registra_fonte"
        assert params["p_company_id"] == COMPANY and params["p_sostituisci"] is False
        valori = params["p_righe"][0]["valori"]
        assert all(isinstance(v, str) for v in valori.values())  # mai float
        assert set(valori) <= set(CAMPI_BILANCIO)

    @pytest.mark.parametrize("detail", ["fonte_non_valida", "azienda_non_trovata",
                                        "righe_non_valide", None])
    async def test_errori_rpc_mappati(self, detail):
        db = FakeDB()
        db.rpc_errors["fn_bilanci_registra_fonte"] = APIError(
            {"message": "x", "code": "P0001", "hint": None, "details": detail}
        )
        with pytest.raises(UpstreamError) as exc:
            await bilanci_service.registra_fonte(
                db, COMPANY, "it_full", bilanci_mapping.da_it_full(it_full_bilanci()), "import"
            )
        assert exc.value.code == "upstream_error"  # nessun dettaglio tecnico all'utente

    async def test_nessuna_riga_nessuna_chiamata_neanche_con_sostituisci(self):
        # Un payload vuoto non deve cancellare lo storico di quella fonte.
        db = FakeDB()
        ok = await bilanci_service._registra_best_effort(
            db, COMPANY, "it_advanced", [], "import", sostituisci=True
        )
        assert ok is True and db.rpcs == []
