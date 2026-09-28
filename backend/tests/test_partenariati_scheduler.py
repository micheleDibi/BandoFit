"""Scheduler del modulo partenariati (WP3): claim giornaliero, passi isolati,
riepilogo, batch spento con budget 0 (nessuna lettura) e batch che si ferma
al primo rifiuto di budget; avvio nel lifespan solo con il modulo acceso."""

import asyncio
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from postgrest.exceptions import APIError

from app.core.errors import AppError
from app.services import partenariati_scheduler as sched
from app.services.partenariato_prompts import PARTENARIATO_PROMPT_VERSION, SCHEMA_VERSION

OGGI = date(2026, 9, 28)


@pytest.fixture(autouse=True)
def stub_settings(monkeypatch):
    for chiave, valore in {
        "PRIMARY_SUPABASE_URL": "https://dummy.supabase.co",
        "PRIMARY_SUPABASE_SERVICE_ROLE_KEY": "k",
        "SECONDARY_SUPABASE_URL": "https://d2.supabase.co",
        "SECONDARY_SUPABASE_ANON_KEY": "k",
    }.items():
        monkeypatch.setenv(chiave, valore)
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def imposta(monkeypatch, **valori):
    from app.core.config import get_settings

    for chiave, valore in valori.items():
        monkeypatch.setenv(chiave, str(valore))
    get_settings.cache_clear()


class FakeQuery:
    def __init__(self, db, tabella):
        self.db, self.tabella, self.op, self.payload, self.filtri = db, tabella, "select", None, {}

    def select(self, *a, **k):
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def eq(self, c, v):
        self.filtri[c] = v
        return self

    def in_(self, c, v):
        self.filtri[c] = list(v)
        return self

    async def execute(self):
        self.db.ops.append((self.tabella, self.op, self.payload, dict(self.filtri)))
        if self.tabella == "partenariati_runs" and self.op == "insert":
            if self.payload["giorno"] in self.db.giorni:
                raise APIError({"message": "dup", "code": "23505", "hint": None, "details": None})
            self.db.giorni.add(self.payload["giorno"])
        if self.tabella == "bando_partenariato" and self.op == "select":
            ids = set(self.filtri.get("bando_id") or [])
            return SimpleNamespace(data=[r for r in self.db.righe if r["bando_id"] in ids])
        return SimpleNamespace(data=[])


class FakePrimary:
    def __init__(self, stale: int = 2):
        self.ops: list = []
        self.rpcs: list = []
        self.giorni: set[str] = set()
        self.righe: list[dict] = []
        self.stale = stale

    def table(self, nome):
        return FakeQuery(self, nome)

    def rpc(self, nome, params):
        self.rpcs.append(nome)
        primary = self

        class _R:
            async def execute(self_inner):
                return SimpleNamespace(data=primary.stale)

        return _R()


class SecondarioVietato:
    def table(self, nome):
        raise AssertionError("il catalogo non va letto")


class FakeCatalogo:
    """Builder PostgREST finto per la lettura dei bandi aperti a pagine."""

    def __init__(self, righe):
        self.righe = righe
        self.query: list = []

    def table(self, nome):
        catalogo = self

        class _Q:
            def __init__(self):
                self.intervallo = (0, 0)
                catalogo.query.append(self)
                self.or_filtri: list = []

            def select(self, *a, **k):
                return self

            def eq(self, *a):
                return self

            @property
            def not_(self):
                return self

            def is_(self, *a):
                return self

            def or_(self, f):
                self.or_filtri.append(f)
                return self

            def order(self, *a, **k):
                return self

            def range(self, inizio, fine):
                self.intervallo = (inizio, fine)
                return self

            async def execute(self):
                inizio, fine = self.intervallo
                return SimpleNamespace(data=catalogo.righe[inizio: fine + 1])

        return _Q()


class FakeAi:
    enabled = True


def riga_bando(id_, forte: bool) -> dict:
    testo = (
        "Le imprese possono partecipare in forma singola o associata mediante ATS."
        if forte else "Contributi per l'acquisto di macchinari."
    )
    return {"id": id_, "slug": f"bando-{id_}", "titolo": f"Bando {id_}", "titolo_breve": None,
            "descrizione_breve": None,
            "contenuto": {"sections": [{"type": "paragraph", "text": testo}]}}


class TestClaim:
    async def test_claim_una_volta_al_giorno(self):
        db = FakePrimary()
        assert await sched.claim_run(db, OGGI) is True
        assert await sched.claim_run(db, OGGI) is False

    async def test_altri_errori_propagano(self):
        class Rotto(FakePrimary):
            def table(self, nome):
                q = FakeQuery(self, nome)

                async def boom():
                    raise APIError({"message": "x", "code": "XX000", "hint": None,
                                    "details": None})

                q.execute = boom
                return q

        with pytest.raises(APIError):
            await sched.claim_run(Rotto(), OGGI)

    async def test_esegui_se_dovuto_rispetta_l_ora(self, monkeypatch):
        imposta(monkeypatch, PARTENARIATI_ORA_ESECUZIONE="05:30")
        db = FakePrimary()
        prima = datetime(2026, 9, 28, 5, 0, tzinfo=ZoneInfo("Europe/Rome"))
        assert await sched.esegui_se_dovuto(db, SecondarioVietato(), FakeAi(), prima) is None
        assert db.ops == []
        dopo = datetime(2026, 9, 28, 6, 0, tzinfo=ZoneInfo("Europe/Rome"))
        esiti = await sched.esegui_se_dovuto(db, SecondarioVietato(), FakeAi(), dopo)
        assert esiti == {"failsafe_estrazioni": 2,
                         "batch_estrazioni": {"eseguite": 0, "motivo": "spento"}}
        # seconda volta nello stesso giorno: già rivendicata
        assert await sched.esegui_se_dovuto(db, SecondarioVietato(), FakeAi(), dopo) is None


class TestPassi:
    async def test_passi_isolati_e_riepilogo_salvato(self, monkeypatch):
        async def esplode(primary):
            raise RuntimeError("guasto")

        chiamato = []

        async def batch(primary, secondary, ai, oggi):
            chiamato.append(oggi)
            return {"eseguite": 0}

        monkeypatch.setattr(sched, "failsafe_estrazioni", esplode)
        monkeypatch.setattr(sched, "batch_estrazioni", batch)
        db = FakePrimary()
        esiti = await sched.esegui_run(db, object(), FakeAi(), OGGI)
        assert esiti == {"failsafe_estrazioni": "errore", "batch_estrazioni": {"eseguite": 0}}
        assert chiamato == [OGGI]
        [aggiornamento] = [op for op in db.ops if op[0] == "partenariati_runs" and op[1] == "update"]
        assert aggiornamento[2] == {"riepilogo": esiti}
        assert aggiornamento[3] == {"giorno": OGGI.isoformat()}

    async def test_failsafe_chiama_la_rpc(self):
        db = FakePrimary(stale=5)
        assert await sched.failsafe_estrazioni(db) == 5
        assert db.rpcs == ["fn_partenariato_chiudi_stale"]


class TestBatch:
    async def test_budget_zero_non_legge_nulla(self):
        esito = await sched.batch_estrazioni(FakePrimary(), SecondarioVietato(), FakeAi(), OGGI)
        assert esito == {"eseguite": 0, "motivo": "spento"}

    async def test_senza_ai_configurata(self, monkeypatch):
        imposta(monkeypatch, PARTENARIATO_BATCH_BUDGET_CENTS_GIORNO=300)
        ai = SimpleNamespace(enabled=False)
        esito = await sched.batch_estrazioni(FakePrimary(), SecondarioVietato(), ai, OGGI)
        assert esito["motivo"] == "ai_non_configurata"

    async def test_solo_bandi_aperti_con_segnali_forti(self):
        catalogo = FakeCatalogo([riga_bando(1, True), riga_bando(2, False), riga_bando(3, True)])
        trovati = await sched._bandi_con_segnali(catalogo, OGGI)
        assert [r["id"] for r in trovati] == [1, 3]
        # segmento aperti del catalogo (stesse guardie dell'elenco)
        assert any("data_scadenza.gte" in f for f in catalogo.query[0].or_filtri)

    async def test_pagine_del_catalogo(self, monkeypatch):
        monkeypatch.setattr(sched, "BATCH_PAGINA", 2)
        catalogo = FakeCatalogo([riga_bando(i, True) for i in range(1, 6)])
        trovati = await sched._bandi_con_segnali(catalogo, OGGI)
        assert [r["id"] for r in trovati] == [1, 2, 3, 4, 5]
        assert [q.intervallo for q in catalogo.query] == [(0, 1), (2, 3), (4, 5)]

    async def test_salta_in_corso_e_freschi(self):
        db = FakePrimary()
        db.righe = [
            {"bando_id": 1, "stato": "in_corso", "esito": None, "prompt_version": None,
             "schema_version": None},
            {"bando_id": 2, "stato": "pronta", "esito": "estratta",
             "prompt_version": PARTENARIATO_PROMPT_VERSION, "schema_version": SCHEMA_VERSION},
            {"bando_id": 3, "stato": "pronta", "esito": "estratta", "prompt_version": 0,
             "schema_version": SCHEMA_VERSION},
        ]
        assert await sched._gia_freschi(db, [1, 2, 3, 4]) == {1, 2}

    async def test_si_ferma_al_budget(self, monkeypatch):
        imposta(monkeypatch, PARTENARIATO_BATCH_BUDGET_CENTS_GIORNO=300)
        catalogo = FakeCatalogo([riga_bando(i, True) for i in (1, 2, 3)])
        chiamate: list = []

        async def fetch(secondary, slug):
            return {"id": int(slug.split("-")[1]), "slug": slug}

        async def esegui(primary, secondary, ai, bando, *, origine, budget_cents):
            chiamate.append((bando["id"], origine, budget_cents))
            if len(chiamate) == 2:
                raise AppError(429, "ai_sospesa_oggi", "sospesa")
            return "estratta"

        monkeypatch.setattr("app.services.bandi_service.fetch_bando_for_ai", fetch)
        monkeypatch.setattr("app.services.partenariato_service.esegui_per_bando", esegui)
        esito = await sched.batch_estrazioni(FakePrimary(), catalogo, FakeAi(), OGGI)
        assert chiamate == [(1, "batch", 300), (2, "batch", 300)]
        assert esito["motivo"] == "budget_esaurito"
        assert esito["esiti"] == {"estratta": 1}

    async def test_un_bando_in_errore_non_ferma_il_batch(self, monkeypatch):
        imposta(monkeypatch, PARTENARIATO_BATCH_BUDGET_CENTS_GIORNO=300)
        catalogo = FakeCatalogo([riga_bando(i, True) for i in (1, 2)])

        async def fetch(secondary, slug):
            return {"id": int(slug.split("-")[1]), "slug": slug}

        async def esegui(primary, secondary, ai, bando, *, origine, budget_cents):
            if bando["id"] == 1:
                raise AppError(429, "partenariato_cooldown", "da poco")
            return "riusata"

        monkeypatch.setattr("app.services.bandi_service.fetch_bando_for_ai", fetch)
        monkeypatch.setattr("app.services.partenariato_service.esegui_per_bando", esegui)
        esito = await sched.batch_estrazioni(FakePrimary(), catalogo, FakeAi(), OGGI)
        assert esito["esiti"] == {"partenariato_cooldown": 1, "riusata": 1}
        assert esito["motivo"] == "completato"


class TestLifespan:
    async def _avvia(self, monkeypatch, attivo: bool, scheduler: bool):
        imposta(
            monkeypatch,
            PARTENARIATI_ATTIVO="true" if attivo else "false",
            PARTENARIATI_SCHEDULER_ATTIVO="true" if scheduler else "false",
            ALERT_SCHEDULER_ATTIVO="false",
            ANTHROPIC_API_KEY="",
            REVOLUT_SECRET_KEY="",
            OPENAPI_API_KEY="",
        )
        import app.main as main

        async def client_finto(settings):
            return object()

        partiti: list = []

        async def run_forever(primary, secondary, ai):
            partiti.append((primary, secondary, ai))
            await asyncio.Event().wait()

        monkeypatch.setattr(main, "create_primary_client", client_finto)
        monkeypatch.setattr(main, "create_secondary_client", client_finto)
        monkeypatch.setattr(sched, "run_forever", run_forever)
        async with main.lifespan(main.app):
            task = main.app.state.partenariati_task
            await asyncio.sleep(0)
            avviato = task is not None and not task.done()
        if task is not None:
            assert task.cancelled() or task.done()
        return avviato, partiti

    async def test_parte_solo_con_modulo_e_scheduler_accesi(self, monkeypatch):
        avviato, partiti = await self._avvia(monkeypatch, True, True)
        assert avviato is True and len(partiti) == 1

    async def test_modulo_spento(self, monkeypatch):
        avviato, partiti = await self._avvia(monkeypatch, False, True)
        assert avviato is False and partiti == []

    async def test_scheduler_spento(self, monkeypatch):
        avviato, partiti = await self._avvia(monkeypatch, True, False)
        assert avviato is False and partiti == []
