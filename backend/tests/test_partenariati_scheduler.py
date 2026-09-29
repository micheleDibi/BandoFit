"""Scheduler del modulo partenariati (WP3): claim giornaliero, passi isolati,
riepilogo, batch spento con budget 0 (nessuna lettura) e batch che si ferma
al primo rifiuto di budget; avvio nel lifespan solo con il modulo acceso.

Passi del WP5 (sul primario finto di test_partner_call_service, gemello delle
RPC della 0037): `failsafe_ai_call` e `chiusura_call` (scadenza della call;
bando chiuso, sospeso, revocato o assente da 7 giorni; azienda non viva;
snapshot del bando; errore del catalogo che non vale come assenza; notifica
al creatore e al titolare con dedup)."""

import asyncio
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from postgrest.exceptions import APIError

from app.core.errors import AppError
from app.services import partenariati_scheduler as sched
from app.services.partenariato_prompts import PARTENARIATO_PROMPT_VERSION, SCHEMA_VERSION

OGGI = date(2026, 9, 28)
NESSUNA_CALL = {"controllate": 0, "chiuse": 0, "bando_mancante": 0, "snapshot_aggiornati": 0,
                "errori": 0, "bandi_letti": True}


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

    def order(self, *a, **k):
        return self

    def range(self, *a):
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


# Passi del WP6 (test propri in test_partenariato_notifiche): qui sostituiti
# da esiti fissi, per verificare solo l'orchestrazione.
ESITI_WP6 = {
    "backfill_collegamenti": {"ricalcolate": 0, "rimosse": 0, "errori": 0},
    "fanout_pendenti": {"call": 0, "completate": 0, "notificate": 0, "errori": 0},
    "digest_settimanale": {"esito": "non_dovuto"},
}


def passi_wp6(monkeypatch):
    async def backfill(primary):
        return ESITI_WP6["backfill_collegamenti"]

    async def fanout(primary, secondary):
        return ESITI_WP6["fanout_pendenti"]

    async def digest(primary, adesso):
        return ESITI_WP6["digest_settimanale"]

    monkeypatch.setattr(sched, "backfill_collegamenti", backfill)
    monkeypatch.setattr(sched, "fanout_pendenti", fanout)
    monkeypatch.setattr(sched, "digest_settimanale", digest)


# Passo del WP9 (test propri qui sotto e, sul primario finto del WP8, in
# test_partenariato_moderazione_service): esito fisso per l'orchestrazione.
ESITI_WP9 = {"ricalcolo_validazioni": {"call": 0, "ricalcolate": 0, "errori": 0}}


def passi_wp9(monkeypatch):
    async def ricalcolo(primary, secondary):
        return ESITI_WP9["ricalcolo_validazioni"]

    monkeypatch.setattr(sched, "ricalcolo_validazioni", ricalcolo)


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
        passi_wp6(monkeypatch)
        passi_wp9(monkeypatch)
        db = FakePrimary()
        prima = datetime(2026, 9, 28, 5, 0, tzinfo=ZoneInfo("Europe/Rome"))
        assert await sched.esegui_se_dovuto(db, SecondarioVietato(), FakeAi(), prima) is None
        assert db.ops == []
        dopo = datetime(2026, 9, 28, 6, 0, tzinfo=ZoneInfo("Europe/Rome"))
        esiti = await sched.esegui_se_dovuto(db, SecondarioVietato(), FakeAi(), dopo)
        assert esiti == {"failsafe_estrazioni": 2, "failsafe_bozze_profilo": 2,
                         "failsafe_ai_call": 2, "failsafe_bozze": 2,
                         "chiusura_call": NESSUNA_CALL,
                         "scadenza_inviti": 2,
                         "batch_estrazioni": {"eseguite": 0, "motivo": "spento"},
                         **ESITI_WP6, **ESITI_WP9}
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
        passi_wp6(monkeypatch)
        passi_wp9(monkeypatch)
        db = FakePrimary()
        esiti = await sched.esegui_run(db, object(), FakeAi(), OGGI)
        assert esiti == {"failsafe_estrazioni": "errore", "failsafe_bozze_profilo": 2,
                         "failsafe_ai_call": 2, "failsafe_bozze": 2,
                         "chiusura_call": NESSUNA_CALL,
                         "scadenza_inviti": 2, "batch_estrazioni": {"eseguite": 0},
                         **ESITI_WP6, **ESITI_WP9}
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


# ------------------------------------------------------------ passi del WP5


def _call_db():
    """Primario e catalogo finti delle call (gemelli della 0037)."""
    from tests.test_partner_call_service import FakeDb, FakeSecondary, riga_bando_pubblico

    return FakeDb(), FakeSecondary, riga_bando_pubblico


def _pubblicata(db, scadenza: date, **modifiche) -> dict:
    return db.con_call(**{"stato": "pubblicata", "pubblicata_at": "2026-09-01T10:00:00+00:00",
                          "scadenza_call": scadenza.isoformat(), **modifiche})


class TestFailsafeAiCall:
    async def test_chiama_la_rpc_con_la_soglia(self, monkeypatch):
        imposta(monkeypatch, PARTNER_CALL_AI_STALE_MINUTI="15")
        db = FakePrimary(stale=3)
        assert await sched.failsafe_ai_call(db) == 3
        assert db.rpcs == ["fn_partner_call_ai_chiudi_stale"]

    async def test_chiude_i_job_orfani(self):
        db, _, _ = _call_db()
        call = db.con_call(ai_testi_stato="in_corso", ai_testi_avviata_at="2026-09-01T10:00:00Z",
                           ai_testi_esecuzione_id="e1")
        db.esecuzioni["e1"] = {
            "id": "e1", "servizio": "partner_call_testi", "gruppo": "altri", "company": "c",
            "owner": call["family_parent_id"], "bando_id": call["bando_id"],
            "richiedente": call["family_parent_id"], "stato": "in_corso",
            "costo_riservato_cents": 7, "cost_cents": None, "input_tokens": 0,
            "output_tokens": 0, "model": None, "errore_codice": None, "llm_eseguito": False,
            "avviata_at": "2026-09-01T10:00:00+00:00",
        }
        assert await sched.failsafe_ai_call(db) == 1
        assert db.call(call["id"])["ai_testi_errore"] == "interrotta"
        [uso] = db.usage
        assert (uso["outcome"], uso["cost_cents"]) == ("timeout_unknown", 7)


class TestChiusuraCall:
    async def test_scadenza_e_notifica_con_dedup(self):
        db, Secondary, riga = _call_db()
        scaduta = _pubblicata(db, OGGI - timedelta(days=1))
        aperta = _pubblicata(db, OGGI, bando_id=202)
        secondary = Secondary(pubblici=[riga(), riga(id=202)])
        esiti = await sched.chiusura_call(db, secondary, OGGI)
        assert esiti["chiuse"] == 1 and esiti["controllate"] == 2
        assert (db.call(scaduta["id"])["stato"], db.call(scaduta["id"])["motivo_chiusura"]) == (
            "scaduta", "scadenza_call")
        assert db.call(aperta["id"])["stato"] == "pubblicata"
        # creatore e titolare coincidono: una notifica sola
        [notifica] = db.tabelle["notifications"]
        assert notifica["tipo"] == "partenariato.call_chiusa"
        assert notifica["dedup_key"] == f"call-chiusa:{scaduta['id']}"
        assert notifica["titolo"] == "Una tua call di partenariato è stata chiusa"
        # una seconda run non trova nulla da chiudere né da notificare
        await sched.chiusura_call(db, secondary, OGGI)
        assert len(db.tabelle["notifications"]) == 1
        assert [a for a in db.audit if a["action"] == "partenariato.call_chiusa"] == [
            {"action": "partenariato.call_chiusa", "actor": None, "call_id": scaduta["id"],
             "motivo": "scadenza_call"}]

    @pytest.mark.parametrize(("visibilita", "avvisate"), [("pubblica", 1), ("solo_invitati", 0)])
    async def test_chi_segue_la_call_riceve_la_chiusura(self, visibilita, avvisate):
        # WP6: il select dello scheduler porta visibilità e sospensione. Chi
        # segue una call visibile a tutti riceve «chiusa»; una call solo su
        # invito (404 per chi la segue) non manda segnali.
        from tests.test_partner_call_service import ALTRA_COMPANY, ALTRO_OWNER

        # il primario finto non proietta le colonne: senza queste, in
        # produzione la call sembrerebbe non visibile
        assert {"visibilita", "sospesa_at", "bando_titolo"} <= set(
            sched.CALL_SELECT_SCHEDULER.split(","))
        db, Secondary, riga = _call_db()
        db.tabelle["profiles"][1].update(email="anna@example.test", is_active=True)
        scaduta = _pubblicata(db, OGGI - timedelta(days=1), visibilita=visibilita)
        db.tabelle.setdefault("partner_call_salvate", []).append(
            {"company_profile_id": ALTRA_COMPANY, "partner_call_id": scaduta["id"],
             "user_id": ALTRO_OWNER})
        await sched.chiusura_call(db, Secondary(pubblici=[riga()]), OGGI)
        chiuse = [n for n in db.tabelle["notifications"]
                  if n["tipo"] == "partenariato.call_seguita_chiusa"]
        assert len(chiuse) == avvisate
        assert all(n["user_id"] == ALTRO_OWNER for n in chiuse)

    async def test_creatore_diverso_dal_titolare(self):
        db, Secondary, riga = _call_db()
        altro = "b0000000-0000-0000-0000-000000000002"
        call = _pubblicata(db, OGGI - timedelta(days=3), creato_da=altro)
        await sched.chiusura_call(db, Secondary(pubblici=[riga()]), OGGI)
        destinatari = {n["user_id"] for n in db.tabelle["notifications"]}
        assert destinatari == {call["family_parent_id"], altro}

    @pytest.mark.parametrize(
        ("stato_bando", "stato", "motivo"),
        [("chiuso", "scaduta", "bando_chiuso"), ("sospeso", "scaduta", "bando_sospeso"),
         ("revocato", "chiusa_annullata", "bando_revocato"), ("Revocato ", "chiusa_annullata",
                                                               "bando_revocato")],
    )
    async def test_stato_live_del_bando(self, stato_bando, stato, motivo):
        db, Secondary, riga = _call_db()
        call = _pubblicata(db, OGGI + timedelta(days=30))
        bozza = db.con_call(company_profile_id="c0000000-0000-0000-0000-000000000002")
        await sched.chiusura_call(db, Secondary(pubblici=[riga(stato_effettivo=stato_bando)]),
                                  OGGI)
        assert (db.call(call["id"])["stato"], db.call(call["id"])["motivo_chiusura"]) == (
            stato, motivo)
        # una bozza non «scade»: si annulla con lo stesso motivo
        assert (db.call(bozza["id"])["stato"], db.call(bozza["id"])["motivo_chiusura"]) == (
            "chiusa_annullata", motivo)

    async def test_bando_assente_sette_giorni(self):
        db, Secondary, _ = _call_db()
        call = _pubblicata(db, OGGI + timedelta(days=60))
        secondary = Secondary(pubblici=[])
        esiti = await sched.chiusura_call(db, secondary, OGGI)
        assert esiti["bando_mancante"] == 1 and esiti["chiuse"] == 0
        assert db.call(call["id"])["bando_mancante_dal"] == OGGI.isoformat()
        # il giorno dopo non si riscrive la data
        esiti = await sched.chiusura_call(db, secondary, OGGI + timedelta(days=1))
        assert esiti["bando_mancante"] == 0
        assert db.call(call["id"])["bando_mancante_dal"] == OGGI.isoformat()
        await sched.chiusura_call(db, secondary, OGGI + timedelta(days=6))
        assert db.call(call["id"])["stato"] == "pubblicata"
        await sched.chiusura_call(db, secondary, OGGI + timedelta(days=7))
        assert (db.call(call["id"])["stato"], db.call(call["id"])["motivo_chiusura"]) == (
            "chiusa_annullata", "bando_non_disponibile")

    async def test_bando_ritrovato_aggiorna_lo_snapshot(self):
        db, Secondary, riga = _call_db()
        call = _pubblicata(db, OGGI + timedelta(days=60),
                           bando_mancante_dal=(OGGI - timedelta(days=2)).isoformat())
        nuova_scadenza = (OGGI + timedelta(days=200)).isoformat()
        esiti = await sched.chiusura_call(
            db, Secondary(pubblici=[riga(stato_effettivo="in apertura prossimamente",
                                         data_scadenza=nuova_scadenza)]), OGGI)
        assert esiti["snapshot_aggiornati"] == 1
        aggiornata = db.call(call["id"])
        assert aggiornata["bando_mancante_dal"] is None
        assert aggiornata["bando_stato_effettivo"] == "in apertura prossimamente"
        assert aggiornata["bando_scadenza"] == nuova_scadenza
        assert aggiornata["stato"] == "pubblicata"

    async def test_snapshot_invariato_nessuna_scrittura(self):
        db, Secondary, riga = _call_db()
        call = _pubblicata(db, OGGI + timedelta(days=60))
        riga_live = riga()
        db.call(call["id"])["bando_scadenza"] = riga_live["data_scadenza"]
        esiti = await sched.chiusura_call(db, Secondary(pubblici=[riga_live]), OGGI)
        assert esiti["snapshot_aggiornati"] == 0
        assert [op for op in db.ops if op[1] == "update"] == []

    async def test_azienda_non_viva(self):
        db, Secondary, riga = _call_db()
        call = _pubblicata(db, OGGI + timedelta(days=60))
        db.tabelle["company_profiles"][0]["archived_at"] = "2026-09-20T10:00:00+00:00"
        await sched.chiusura_call(db, Secondary(pubblici=[riga()]), OGGI)
        assert (db.call(call["id"])["stato"], db.call(call["id"])["motivo_chiusura"]) == (
            "chiusa_annullata", "azienda_non_disponibile")

    async def test_catalogo_in_errore_non_vale_come_assenza(self):
        db, Secondary, _ = _call_db()
        scaduta = _pubblicata(db, OGGI - timedelta(days=1))
        aperta = _pubblicata(db, OGGI + timedelta(days=9), bando_id=202)
        secondary = Secondary()
        secondary.guasto_pubblico = RuntimeError("rete")
        esiti = await sched.chiusura_call(db, secondary, OGGI)
        assert esiti["bandi_letti"] is False and esiti["bando_mancante"] == 0
        assert db.call(scaduta["id"])["stato"] == "scaduta"  # la scadenza non dipende dal bando
        assert db.call(aperta["id"])["stato"] == "pubblicata"
        assert db.call(aperta["id"])["bando_mancante_dal"] is None
        # nemmeno una call con il bando già mancante da più di 7 giorni
        db.call(aperta["id"])["bando_mancante_dal"] = (OGGI - timedelta(days=10)).isoformat()
        await sched.chiusura_call(db, secondary, OGGI)
        assert db.call(aperta["id"])["stato"] == "pubblicata"

    async def test_una_call_in_errore_non_ferma_le_altre(self, monkeypatch):
        db, Secondary, riga = _call_db()
        prima = _pubblicata(db, OGGI - timedelta(days=1))
        seconda = _pubblicata(db, OGGI - timedelta(days=1), bando_id=202)
        originale = db._fn_partner_call_chiudi_auto

        def chiudi(p):
            if p["p_call"] == prima["id"]:
                raise RuntimeError("guasto")
            return originale(p)

        db._fn_partner_call_chiudi_auto = chiudi
        esiti = await sched.chiusura_call(db, Secondary(pubblici=[riga()]), OGGI)
        assert esiti["errori"] == 1 and esiti["chiuse"] == 1
        assert db.call(seconda["id"])["stato"] == "scaduta"

    async def test_aggiornamenti_a_blocchi_isolati(self, monkeypatch):
        """Un errore negli aggiornamenti di `bando_mancante_dal` o dello
        snapshot del bando non fa uscire il passo: le chiusure già fatte e gli
        altri blocchi restano nel riepilogo, l'errore si conta."""
        monkeypatch.setattr(sched, "_BLOCCO_ID", 1)
        db, Secondary, riga = _call_db()
        scaduta = _pubblicata(db, OGGI - timedelta(days=1))
        _pubblicata(db, OGGI + timedelta(days=30), bando_id=202)  # bando assente
        _pubblicata(db, OGGI + timedelta(days=30), bando_id=203)  # bando assente
        cambiata = _pubblicata(db, OGGI + timedelta(days=30), bando_id=204)
        live = riga(id=204, stato_effettivo="in apertura prossimamente")
        db.guasti[("partner_calls", "update")] = RuntimeError("postgrest")
        esiti = await sched.chiusura_call(db, Secondary(pubblici=[riga(), live]), OGGI)
        assert esiti["chiuse"] == 1 and db.call(scaduta["id"])["stato"] == "scaduta"
        assert esiti["errori"] == 3  # due blocchi di mancanti e un bando da aggiornare
        assert esiti["bando_mancante"] == 0 and esiti["snapshot_aggiornati"] == 0
        # senza guasti la run dopo completa il lavoro
        del db.guasti[("partner_calls", "update")]
        esiti = await sched.chiusura_call(db, Secondary(pubblici=[live]), OGGI)
        assert esiti["errori"] == 0 and esiti["bando_mancante"] == 2
        assert esiti["snapshot_aggiornati"] == 1
        assert db.call(cambiata["id"])["bando_stato_effettivo"] == "in apertura prossimamente"

    async def test_pagine(self, monkeypatch):
        monkeypatch.setattr(sched, "CALL_PAGINA", 2)
        db, Secondary, riga = _call_db()
        for i in range(5):
            _pubblicata(db, OGGI - timedelta(days=1), bando_id=101 + i)
        esiti = await sched.chiusura_call(db, Secondary(pubblici=[]), OGGI)
        assert esiti["controllate"] == 5 and esiti["chiuse"] == 5

    async def test_chiuse_e_sospese_non_si_toccano(self):
        db, Secondary, _ = _call_db()
        for stato in ("scaduta", "chiusa_completata", "chiusa_annullata", "sospesa_moderazione"):
            db.con_call(stato=stato)
        esiti = await sched.chiusura_call(db, Secondary(pubblici=[]), OGGI)
        assert esiti["controllate"] == 0
        assert db.chiamate("fn_partner_call_chiudi_auto") == []


class TestRicalcoloValidazioni:
    """Passo del WP9: le call da rivalidare dalla RPC della 0041, ognuna
    isolata; il ricalcolo vero (vista del creatore, salvataggio) è verificato
    sul primario finto del WP8 in test_partenariato_moderazione_service."""

    class _Primario:
        def __init__(self, dati):
            self.dati, self.chiamate = dati, []

        def rpc(self, nome, params):
            self.chiamate.append((nome, params))
            primario = self

            class _R:
                async def execute(self_inner):
                    return SimpleNamespace(data=primario.dati)

            return _R()

    async def test_blocco_limitato_e_call_isolate(self, monkeypatch):
        fatte = []

        async def ricalcola(primary, secondary, call_id):
            fatte.append(call_id)
            if call_id == "c2":
                raise RuntimeError("guasto")
            return call_id != "c3"

        monkeypatch.setattr(sched.partenariato_consorzio_service, "ricalcola_validazione",
                            ricalcola)
        db = self._Primario(["c1", "c2", {"fn_partner_call_validazioni_da_ricalcolare": "c3"}])
        esito = await sched.ricalcolo_validazioni(db, object())
        assert esito == {"call": 3, "ricalcolate": 1, "errori": 1}
        assert fatte == ["c1", "c2", "c3"]
        assert db.chiamate == [("fn_partner_call_validazioni_da_ricalcolare",
                                {"p_limite": sched.RICALCOLO_VALIDAZIONI_LIMITE})]
        assert sched.RICALCOLO_VALIDAZIONI_LIMITE == 100

    async def test_nessuna_call(self, monkeypatch):
        async def vietato(*a):
            raise AssertionError("nessun ricalcolo atteso")

        monkeypatch.setattr(sched.partenariato_consorzio_service, "ricalcola_validazione",
                            vietato)
        for dati in ([], None, 3):
            assert await sched.ricalcolo_validazioni(self._Primario(dati), object()) == {
                "call": 0, "ricalcolate": 0, "errori": 0}

    async def test_nella_run_del_giorno_dopo_il_backfill(self, monkeypatch):
        ordine = []
        for nome in ("failsafe_estrazioni", "failsafe_bozze_profilo", "failsafe_ai_call",
                     "backfill_collegamenti"):
            async def passo(primary, _n=nome):
                ordine.append(_n)
                return 0
            monkeypatch.setattr(sched, nome, passo)

        async def chiusura(primary, secondary, oggi):
            ordine.append("chiusura_call")
            return 0

        async def inviti(primary):
            ordine.append("scadenza_inviti")
            return 0

        async def batch(primary, secondary, ai, oggi):
            ordine.append("batch_estrazioni")
            return 0

        async def ricalcolo(primary, secondary):
            ordine.append("ricalcolo_validazioni")
            return {"call": 0, "ricalcolate": 0, "errori": 0}

        async def fanout(primary, secondary):
            ordine.append("fanout_pendenti")
            return 0

        async def digest(primary, adesso):
            ordine.append("digest_settimanale")
            return 0

        for nome, fn in (("chiusura_call", chiusura), ("scadenza_inviti", inviti),
                         ("batch_estrazioni", batch), ("ricalcolo_validazioni", ricalcolo),
                         ("fanout_pendenti", fanout), ("digest_settimanale", digest)):
            monkeypatch.setattr(sched, nome, fn)
        esiti = await sched.esegui_run(FakePrimary(), object(), FakeAi(), OGGI)
        assert esiti["ricalcolo_validazioni"] == {"call": 0, "ricalcolate": 0, "errori": 0}
        assert ordine.index("ricalcolo_validazioni") == ordine.index("backfill_collegamenti") + 1
