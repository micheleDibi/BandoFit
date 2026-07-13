"""Alert nuovi bandi: calcolo puro (date iniettate), gate per destinatario,
ledger idempotente e run completa con contatori."""

from datetime import date
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.schemas.bando import LookupsOut
from app.schemas.common import AtecoItem, LookupItem
from app.services import bando_alert_service as svc
from app.services.compatibility import CompanyFacets

ROMA = ZoneInfo("Europe/Rome")
OGGI = date(2026, 7, 13)
OWNER = "aaaaaaaa-0000-0000-0000-000000000040"
FIGLIO = "bbbbbbbb-0000-0000-0000-000000000041"
COMPANY_ID = "cccccccc-0000-0000-0000-000000000042"


def lookups() -> LookupsOut:
    return LookupsOut(
        regioni=[LookupItem(id=1, nome="Lombardia"), LookupItem(id=2, nome="Lazio")],
        settori=[LookupItem(id=5, nome="Manifattura")],
        beneficiari=[LookupItem(id=9, nome="PMI")],
        codici_ateco=[AtecoItem(id=10, codice="62", descrizione="Software")],
        tipologie_bando=[],
        modalita_erogazione=[],
        programmi=[],
    )


def facets_ok() -> CompanyFacets:
    return CompanyFacets(
        regioni_ids={1},
        ateco_ids={10},
        settore_id=5,
        beneficiari_ids=set(),
        sufficiente=True,
    )


def bando_row(**overrides) -> dict:
    base = {
        "id": 7,
        "slug": "bando-di-prova",
        "titolo": "Bando di prova per PMI",
        "titolo_breve": "Bando di prova",
        "ente_erogatore": "Regione Lombardia",
        "importo_totale_eur": 1_000_000,
        "importo_max_per_progetto_eur": None,
        "data_pubblicazione": "2026-07-10",
        "data_scadenza": "2026-09-30",
        "created_at": "2026-07-11T06:00:00+00:00",
        "bando_regioni": [{"regione_id": 1}],
        "bando_codici_ateco": [{"codice_ateco_id": 10}],
        "bando_settori": [],
        "bando_beneficiari": [],
    }
    base.update(overrides)
    return base


def candidato(**overrides) -> svc.BandoCandidato:
    [c], scartati = svc.filtra_candidati(
        [bando_row(**overrides)],
        oggi=OGGI,
        attivazione=date(2026, 7, 1),
        orizzonte_giorni=60,
        fuso=ROMA,
    )
    assert scartati == 0
    return c


# ---------------------------------------------------------------------------
# Funzioni pure
# ---------------------------------------------------------------------------


class TestDataRiferimento:
    def test_pubblicazione_ufficiale(self):
        assert svc.data_riferimento(bando_row(), ROMA) == date(2026, 7, 10)

    def test_fallback_ingestione_in_data_italiana(self):
        # 22:30 UTC del 1° luglio = 00:30 del 2 luglio a Roma (UTC+2 d'estate).
        row = bando_row(
            data_pubblicazione=None, created_at="2026-07-01T22:30:00+00:00"
        )
        assert svc.data_riferimento(row, ROMA) == date(2026, 7, 2)

    def test_nessuna_data(self):
        row = bando_row(data_pubblicazione=None, created_at=None)
        assert svc.data_riferimento(row, ROMA) is None


class TestFiltraCandidati:
    def test_gate_attivazione(self):
        """No-backfill: bandi anteriori all'attivazione della feature esclusi."""
        rows = [bando_row(data_pubblicazione="2026-06-30")]
        candidati, scartati = svc.filtra_candidati(
            rows, oggi=OGGI, attivazione=date(2026, 7, 1), orizzonte_giorni=60, fuso=ROMA
        )
        assert candidati == [] and scartati == 0  # prima dell'attivazione: non conta

    def test_orizzonte_conteggiato(self):
        rows = [bando_row(data_pubblicazione="2026-03-01")]
        candidati, scartati = svc.filtra_candidati(
            rows, oggi=OGGI, attivazione=date(2026, 1, 1), orizzonte_giorni=60, fuso=ROMA
        )
        assert candidati == [] and scartati == 1  # troncato: mai in silenzio

    def test_pubblicazione_futura_esclusa(self):
        rows = [bando_row(data_pubblicazione="2026-07-20")]
        candidati, _ = svc.filtra_candidati(
            rows, oggi=OGGI, attivazione=date(2026, 7, 1), orizzonte_giorni=60, fuso=ROMA
        )
        assert candidati == []


class TestBandiEleggibili:
    def eleggibili(self, *, pubblicazione: str, ritardo: int, oggi: date = OGGI):
        return svc.bandi_eleggibili(
            [candidato(data_pubblicazione=pubblicazione)],
            facets_ok(),
            totale_regioni=2,
            ritardo_giorni=ritardo,
            oggi=oggi,
        )

    def test_ritardo_maturato_al_confine(self):
        # pubblicato il 6, ritardo 7 → idoneo dal 13 (oggi) incluso.
        assert len(self.eleggibili(pubblicazione="2026-07-06", ritardo=7)) == 1

    def test_ritardo_non_maturato(self):
        assert self.eleggibili(pubblicazione="2026-07-07", ritardo=7) == []

    def test_ritardo_zero_stesso_giorno(self):
        assert len(self.eleggibili(pubblicazione="2026-07-13", ritardo=0)) == 1

    def test_ingestione_tardiva_invia_subito(self):
        # pubblicazione + ritardo già passati da un pezzo: primo run utile.
        assert len(self.eleggibili(pubblicazione="2026-07-01", ritardo=1)) == 1

    def test_punteggio_67_incluso(self):
        # regioni sì, ateco sì, settori no → 2/3 = 67 >= 60.
        c = candidato(bando_settori=[{"settore_id": 99}])
        out = svc.bandi_eleggibili(
            [c], facets_ok(), totale_regioni=2, ritardo_giorni=1, oggi=OGGI
        )
        assert len(out) == 1
        assert out[0][1]["punteggio"] == 67

    def test_punteggio_50_escluso(self):
        # regioni sì, ateco no → 1/2 = 50 < 60.
        c = candidato(bando_codici_ateco=[{"codice_ateco_id": 999}])
        assert (
            svc.bandi_eleggibili(
                [c], facets_ok(), totale_regioni=2, ritardo_giorni=1, oggi=OGGI
            )
            == []
        )

    def test_facets_insufficienti_esclusi(self):
        insuff = CompanyFacets(sufficiente=False)
        assert (
            svc.bandi_eleggibili(
                [candidato()], insuff, totale_regioni=2, ritardo_giorni=1, oggi=OGGI
            )
            == []
        )


class TestMotivo:
    def test_nomi_risolti(self):
        c = candidato(bando_settori=[{"settore_id": 5}])
        [(_, compat)] = svc.bandi_eleggibili(
            [c], facets_ok(), totale_regioni=2, ritardo_giorni=1, oggi=OGGI
        )
        motivo = svc.motivo_compatibilita(compat, lookups())
        assert "Regioni: Lombardia" in motivo
        assert "ATECO: 62" in motivo
        assert "Settore: Manifattura" in motivo

    def test_bando_nazionale(self):
        c = candidato(bando_regioni=[{"regione_id": 1}, {"regione_id": 2}])
        [(_, compat)] = svc.bandi_eleggibili(
            [c], facets_ok(), totale_regioni=2, ritardo_giorni=1, oggi=OGGI
        )
        assert "Aperto a tutta Italia" in svc.motivo_compatibilita(compat, lookups())


class TestGiorniAllaScadenza:
    def test_valori(self):
        assert svc.giorni_alla_scadenza(None, OGGI) is None
        assert svc.giorni_alla_scadenza(OGGI, OGGI) == 0
        assert svc.giorni_alla_scadenza(date(2026, 7, 27), OGGI) == 14


# ---------------------------------------------------------------------------
# Fake PostgREST (select/upsert/update/rpc con not_/or_/is_)
# ---------------------------------------------------------------------------


class FakeQuery:
    def __init__(self, owner, table: str):
        self._owner = owner
        self._table = table
        self._action = "select"
        self._payload = None
        self.filters: list = []

    def select(self, *args, **kwargs):
        return self

    def update(self, payload):
        self._action = "update"
        self._payload = payload
        return self

    def upsert(self, payload, **kwargs):
        self._action = "upsert"
        self._payload = payload
        self.filters.append(("upsert_opts", kwargs))
        return self

    @property
    def not_(self):
        self.filters.append(("not",))
        return self

    def is_(self, column, value):
        self.filters.append(("is", column, value))
        return self

    def or_(self, expr):
        self.filters.append(("or", expr))
        return self

    def eq(self, column, value):
        self.filters.append(("eq", column, value))
        return self

    def gte(self, column, value):
        self.filters.append(("gte", column, value))
        return self

    def in_(self, column, values):
        self.filters.append(("in", column, list(values)))
        return self

    def limit(self, n):
        return self

    def order(self, *args, **kwargs):
        return self

    async def execute(self):
        self._owner.ops.append((self._table, self._action, self._payload, list(self.filters)))
        if self._action == "select":
            queue = self._owner.select_queues.get(self._table)
            if queue:
                return SimpleNamespace(data=queue.pop(0))
            return SimpleNamespace(data=self._owner.selects.get(self._table, []))
        if self._action == "upsert":
            preset = self._owner.upsert_results.get(self._table)
            if preset is not None:
                return SimpleNamespace(data=preset)
            rows = self._payload if isinstance(self._payload, list) else [self._payload]
            out = []
            for row in rows:
                self._owner.next_id += 1
                out.append({"id": self._owner.next_id, **row})
            return SimpleNamespace(data=out)
        return SimpleNamespace(data=self._owner.updates.get(self._table, []))


class FakeRpc:
    def __init__(self, owner, fn: str, params: dict):
        self._owner = owner
        self._fn = fn
        self._params = params

    async def execute(self):
        self._owner.rpc_calls.append((self._fn, self._params))
        return SimpleNamespace(data=self._owner.rpc_results.get(self._fn, []))


class FakeClient:
    def __init__(self, selects: dict | None = None):
        self.selects = selects or {}
        self.select_queues: dict = {}
        self.updates: dict = {}
        self.upsert_results: dict = {}
        self.rpc_results: dict = {}
        self.ops: list = []
        self.rpc_calls: list = []
        self.next_id = 100

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self, name)

    def rpc(self, fn: str, params: dict) -> FakeRpc:
        return FakeRpc(self, fn, params)


@pytest.fixture(autouse=True)
def stub_settings(monkeypatch):
    for key, value in {
        "PRIMARY_SUPABASE_URL": "https://dummy.supabase.co",
        "PRIMARY_SUPABASE_SERVICE_ROLE_KEY": "k",
        "SECONDARY_SUPABASE_URL": "https://d2.supabase.co",
        "SECONDARY_SUPABASE_ANON_KEY": "k",
        "ALERT_DATA_ATTIVAZIONE": "2026-07-01",
        "ALERT_PAUSA_INVII_SECONDI": "0",
        "FRONTEND_URL": "https://app.test.it",
        "API_PUBLIC_URL": "https://api.test.it/api/v1",
    }.items():
        monkeypatch.setenv(key, value)
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def email_calls(monkeypatch):
    calls: list[dict] = []

    async def fake_send(to_email, bandi, cta_url, unsubscribe_url):
        calls.append(
            {"to": to_email, "bandi": bandi, "cta": cta_url, "unsubscribe": unsubscribe_url}
        )
        return True

    monkeypatch.setattr(svc.email_service, "send_bandi_digest_email", fake_send)
    return calls


@pytest.fixture
def notify_calls(monkeypatch):
    calls: list[dict] = []

    async def fake_notify(primary, user_ids, **kwargs):
        calls.append({"user_ids": [str(u) for u in user_ids], **kwargs})

    monkeypatch.setattr(svc.notification_service, "notify", fake_notify)
    return calls


@pytest.fixture
def stub_lookups(monkeypatch):
    async def fake_get_lookups(secondary):
        return lookups()

    monkeypatch.setattr(svc.lookup_service, "get_lookups", fake_get_lookups)


# ---------------------------------------------------------------------------
# Gate e ledger
# ---------------------------------------------------------------------------


class TestGatePiano:
    async def test_solo_piani_con_alert_e_ritardo(self):
        primary = FakeClient(
            selects={
                "user_subscriptions": [
                    {"user_id": "a", "subscription_plans": {"alert_attivo": True, "alert_ritardo_giorni": 7}},
                    {"user_id": "b", "subscription_plans": {"alert_attivo": False, "alert_ritardo_giorni": 7}},
                    {"user_id": "c", "subscription_plans": {"alert_attivo": True, "alert_ritardo_giorni": None}},
                    {"user_id": "d", "subscription_plans": None},
                ]
            }
        )
        assert await svc.carica_ritardi_piano(primary, ["a", "b", "c", "d"]) == {"a": 7}


class TestDestinatari:
    async def test_owner_e_figli_attivi(self):
        primary = FakeClient(
            selects={
                "family_members": [{"parent_id": OWNER, "member_id": FIGLIO}],
                "profiles": [
                    {"id": OWNER, "email": "own@test.it", "is_active": True},
                    {"id": FIGLIO, "email": "figlio@test.it", "is_active": True},
                ],
            }
        )
        per_owner = await svc.carica_destinatari(primary, [OWNER])
        assert {p["id"] for p in per_owner[OWNER]} == {OWNER, FIGLIO}

    async def test_disattivati_esclusi(self):
        primary = FakeClient(
            selects={
                "family_members": [],
                "profiles": [{"id": OWNER, "email": "own@test.it", "is_active": False}],
            }
        )
        assert await svc.carica_destinatari(primary, [OWNER]) == {}


class TestRecapitabili:
    async def test_verifica_email_e_suppression(self):
        primary = FakeClient(
            selects={"email_suppressions": [{"email": "Sospeso@Test.it"}]}
        )
        primary.rpc_results["fn_email_verificate"] = [OWNER, FIGLIO]
        destinatari = [
            {"id": OWNER, "email": "own@test.it"},
            {"id": FIGLIO, "email": "sospeso@test.it"},   # soppresso (case-insensitive)
            {"id": "x", "email": "nonverificata@test.it"},  # non nel risultato RPC
        ]
        out = await svc.filtra_recapitabili(primary, destinatari)
        assert [d["id"] for d in out] == [OWNER]


class TestClaimLedger:
    def eleggibile(self) -> list:
        return [(candidato(), {"punteggio": 100})]

    async def test_nuova_coppia_claimata(self):
        primary = FakeClient(selects={"bando_alert_sends": []})
        claimed = await svc.claim_ledger(
            primary, OWNER, self.eleggibile(), oggi=OGGI, max_tentativi=3
        )
        assert claimed == {7: 101}

    async def test_inviata_mai_ritentata(self):
        primary = FakeClient(
            selects={
                "bando_alert_sends": [
                    {"id": 1, "bando_id": 7, "stato": "inviata", "tentativi": 1}
                ]
            }
        )
        assert (
            await svc.claim_ledger(primary, OWNER, self.eleggibile(), oggi=OGGI, max_tentativi=3)
            == {}
        )

    async def test_fallita_ritentabile(self):
        primary = FakeClient(
            selects={
                "bando_alert_sends": [
                    {"id": 1, "bando_id": 7, "stato": "fallita", "tentativi": 1}
                ]
            }
        )
        primary.updates["bando_alert_sends"] = [{"id": 1}]
        claimed = await svc.claim_ledger(
            primary, OWNER, self.eleggibile(), oggi=OGGI, max_tentativi=3
        )
        assert claimed == {7: 1}
        update_op = next(
            op for op in primary.ops if op[0] == "bando_alert_sends" and op[1] == "update"
        )
        assert update_op[2]["stato"] == "in_invio"
        assert update_op[2]["tentativi"] == 2
        assert ("eq", "stato", "fallita") in update_op[3]

    async def test_fallita_esausta_e_incerta_skip(self):
        primary = FakeClient(
            selects={
                "bando_alert_sends": [
                    {"id": 1, "bando_id": 7, "stato": "fallita", "tentativi": 3},
                ]
            }
        )
        assert (
            await svc.claim_ledger(primary, OWNER, self.eleggibile(), oggi=OGGI, max_tentativi=3)
            == {}
        )


# ---------------------------------------------------------------------------
# Run completa
# ---------------------------------------------------------------------------


def primary_per_run(*, abilitati: bool = True) -> FakeClient:
    primary = FakeClient(
        selects={
            "company_profiles": [
                {
                    "id": COMPANY_ID,
                    "parent_id": OWNER,
                    "ateco_id": 10,
                    "settore_id": None,
                    "regione_id": 1,
                    "beneficiari": [],
                }
            ],
            "company_data": [{"company_profile_id": COMPANY_ID, "derived": None}],
            "user_subscriptions": [
                {
                    "user_id": OWNER,
                    "subscription_plans": {"alert_attivo": True, "alert_ritardo_giorni": 1},
                }
            ],
            "family_members": [],
            "profiles": [{"id": OWNER, "email": "own@test.it", "is_active": True}],
            "email_suppressions": [],
            "bando_alert_settings": [
                {"user_id": OWNER, "abilitati": abilitati, "unsubscribe_token": "tok-1"}
            ],
            "bando_alert_sends": [],
        }
    )
    primary.updates["bando_alert_sends"] = []
    primary.rpc_results["fn_email_verificate"] = [OWNER]
    return primary


class TestEseguiRun:
    async def test_happy_path(self, email_calls, notify_calls, stub_lookups):
        primary = primary_per_run()
        secondary = FakeClient(selects={"bando": [bando_row()]})
        riepilogo = await svc.esegui_run(primary, secondary, OGGI)

        assert riepilogo["esito"] == "ok"
        assert riepilogo["bandi_candidati"] == 1
        assert riepilogo["destinatari"] == 1
        assert riepilogo["email_inviate"] == 1
        assert riepilogo["email_fallite"] == 0

        [email] = email_calls
        assert email["to"] == "own@test.it"
        assert email["unsubscribe"] == (
            "https://api.test.it/api/v1/alerts/unsubscribe?token=tok-1"
        )
        [item] = email["bandi"]
        assert item["url"] == "https://app.test.it/app/bandi/bando-di-prova"
        assert "Regioni: Lombardia" in item["motivo"]

        # Ledger finalizzato «inviata» sulla riga claimata.
        finalizza = [
            op
            for op in primary.ops
            if op[0] == "bando_alert_sends" and op[1] == "update" and op[2].get("stato") == "inviata"
        ]
        assert len(finalizza) == 1

        [notifica] = notify_calls
        assert notifica["dedup_key"] == f"bando-alert:{OGGI.isoformat()}"
        assert notifica["url"] == "/app/bandi"

        # Run row con i contatori.
        run_upsert = next(
            op for op in primary.ops if op[0] == "bando_alert_runs" and op[1] == "upsert"
        )
        assert run_upsert[2]["email_inviate"] == 1

    async def test_opt_out_rispettato(self, email_calls, notify_calls, stub_lookups):
        primary = primary_per_run(abilitati=False)
        secondary = FakeClient(selects={"bando": [bando_row()]})
        riepilogo = await svc.esegui_run(primary, secondary, OGGI)
        assert riepilogo["destinatari"] == 0
        assert email_calls == []

    async def test_invio_fallito_conteggiato(self, notify_calls, stub_lookups, monkeypatch):
        async def fake_send(*args, **kwargs):
            return False

        monkeypatch.setattr(svc.email_service, "send_bandi_digest_email", fake_send)
        primary = primary_per_run()
        secondary = FakeClient(selects={"bando": [bando_row()]})
        riepilogo = await svc.esegui_run(primary, secondary, OGGI)
        assert riepilogo["email_fallite"] == 1
        fallita = [
            op
            for op in primary.ops
            if op[0] == "bando_alert_sends" and op[1] == "update" and op[2].get("stato") == "fallita"
        ]
        assert len(fallita) == 1
        assert notify_calls == []  # niente notifica in-app senza email

    async def test_errore_non_solleva(self, stub_lookups):
        class BrokenSecondary(FakeClient):
            def table(self, name):
                raise RuntimeError("secondario giù")

        primary = primary_per_run()
        riepilogo = await svc.esegui_run(primary, BrokenSecondary(), OGGI)
        assert riepilogo["esito"] == "errore"
        assert "secondario giù" in riepilogo["dettagli"]["errore"]


class TestImpostazioni:
    async def test_get_default_abilitati(self):
        primary = FakeClient(selects={"bando_alert_settings": []})
        assert await svc.get_abilitati(primary, OWNER) is True

    async def test_unsubscribe_idempotente(self):
        primary = FakeClient()
        primary.updates["bando_alert_settings"] = []
        await svc.unsubscribe_by_token(primary, "token-ignoto")  # nessun raise
        [(_, action, payload, filters)] = [
            op for op in primary.ops if op[0] == "bando_alert_settings"
        ]
        assert action == "update"
        assert payload == {"abilitati": False}
        assert ("eq", "unsubscribe_token", "token-ignoto") in filters