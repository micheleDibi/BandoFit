"""Alert nuovi bandi: calcolo puro (date iniettate), gate per destinatario,
ledger idempotente e run completa con contatori."""

import logging
from datetime import date
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from postgrest.exceptions import APIError

from app.schemas.bando import LookupsOut
from app.schemas.common import AtecoItem, LookupItem
from app.services import bando_alert_service as svc
from app.services.compatibility import CompanyFacets

ROMA = ZoneInfo("Europe/Rome")
OGGI = date(2026, 7, 13)
OWNER = "aaaaaaaa-0000-0000-0000-000000000040"
FIGLIO = "bbbbbbbb-0000-0000-0000-000000000041"
COMPANY_ID = "cccccccc-0000-0000-0000-000000000042"
COMPANY_A = "cccccccc-0000-0000-0000-0000000000a1"
COMPANY_B = "cccccccc-0000-0000-0000-0000000000b2"


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
        self._count = None
        self._range: tuple[int, int] | None = None
        self.filters: list = []

    def select(self, *args, **kwargs):
        self._count = kwargs.get("count")
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

    def gt(self, column, value):
        self.filters.append(("gt", column, value))
        return self

    def in_(self, column, values):
        self.filters.append(("in", column, list(values)))
        return self

    def limit(self, n):
        return self

    def order(self, *args, **kwargs):
        self.filters.append(("order", args, kwargs))
        return self

    def range(self, start, end):
        self._range = (start, end)
        self.filters.append(("range", start, end))
        return self

    async def execute(self):
        self._owner.ops.append((self._table, self._action, self._payload, list(self.filters)))
        if self._action == "select":
            queue = self._owner.select_queues.get(self._table)
            if queue:
                return SimpleNamespace(data=queue.pop(0))
            # Una tabella non prevista dal test è un errore, non una lista
            # vuota: così una lettura sulla tabella sbagliata non passa inosservata.
            if self._table not in self._owner.selects:
                raise AssertionError(f"tabella inattesa: {self._table}")
            righe = self._owner.selects[self._table]
            for filtro in self.filters:
                if filtro[0] == "gt":  # keyset: solo le righe dopo l'ultima chiave letta
                    righe = [r for r in righe if r.get(filtro[1]) is not None
                             and r[filtro[1]] > filtro[2]]
            totale = len(righe)
            if self._range is not None:
                inizio, fine = self._range
                righe = righe[inizio : fine + 1]
            if self._owner.max_rows is not None:
                righe = righe[: self._owner.max_rows]  # come il max-rows di PostgREST
            count = totale if self._count == "exact" else None
            return SimpleNamespace(data=righe, count=count)
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
        self.max_rows: int | None = None

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self, name)

    def rpc(self, fn: str, params: dict) -> FakeRpc:
        return FakeRpc(self, fn, params)


def errore_postgrest(codice: str) -> APIError:
    return APIError({"message": "errore del catalogo", "code": codice, "hint": None,
                     "details": None})


class FakeQueryConErrori(FakeQuery):
    """Esaurite le risposte in coda, ogni select solleva il prossimo errore."""

    async def execute(self):
        if self._action == "select" and not self._owner.select_queues.get(self._table):
            self._owner.ops.append((self._table, self._action, None, list(self.filters)))
            raise self._owner.errori.pop(0)
        return await super().execute()


class FakeClientConErrori(FakeClient):
    """Catalogo che risponde alla prima pagina (senza conteggio) e poi con gli
    errori dati, uno per richiesta."""

    def __init__(self, prima: list[dict], *errori: Exception):
        super().__init__()
        self.select_queues["bando_pubblico"] = [prima]
        self.errori = list(errori)

    def table(self, name: str) -> FakeQuery:
        return FakeQueryConErrori(self, name)


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
def email_multi_calls(monkeypatch):
    calls: list[dict] = []

    async def fake_send_multi(to_email, sezioni, cta_url, unsubscribe_url):
        calls.append(
            {"to": to_email, "sezioni": sezioni, "cta": cta_url, "unsubscribe": unsubscribe_url}
        )
        return True

    monkeypatch.setattr(svc.email_service, "send_bandi_digest_email_multi", fake_send_multi)
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
            selects={"email_suppressions": [{"id": 1, "email": "Sospeso@Test.it"}]}
        )
        primary.rpc_results["fn_email_verificate"] = [OWNER, FIGLIO]
        destinatari = [
            {"id": OWNER, "email": "own@test.it"},
            {"id": FIGLIO, "email": "sospeso@test.it"},   # soppresso (case-insensitive)
            {"id": "x", "email": "nonverificata@test.it"},  # non nel risultato RPC
        ]
        out = await svc.filtra_recapitabili(primary, destinatari)
        assert [d["id"] for d in out] == [OWNER]

    async def test_suppression_list_oltre_le_1000_righe(self):
        # Due pagine a keyset: l'indirizzo soppresso sta nella seconda.
        primary = FakeClient(selects={"email_suppressions": []})
        primary.select_queues["email_suppressions"] = [
            [{"id": i, "email": f"altro{i}@test.it"} for i in range(1, 1001)],
            [{"id": 1001, "email": "Sospeso@Test.it"}],
        ]
        primary.rpc_results["fn_email_verificate"] = [OWNER, FIGLIO]
        destinatari = [
            {"id": OWNER, "email": "own@test.it"},
            {"id": FIGLIO, "email": "sospeso@test.it"},
        ]
        out = await svc.filtra_recapitabili(primary, destinatari)
        assert [d["id"] for d in out] == [OWNER]
        letture = [op for op in primary.ops if op[0] == "email_suppressions"]
        assert len(letture) == 3  # l'ultima, vuota, chiude la lettura
        assert ("gt", "id", 1000) in letture[1][3]
        assert ("gt", "id", 1001) in letture[2][3]

    async def test_suppression_list_con_un_max_rows_piu_basso(self):
        # Una pagina corta non chiude la lettura: solo una pagina vuota.
        righe = [{"id": i, "email": f"altro{i}@test.it"} for i in range(1, 1200)]
        primary = FakeClient(selects={"email_suppressions": [
            *righe, {"id": 1200, "email": "Sospeso@Test.it"}]})
        primary.max_rows = 500
        primary.rpc_results["fn_email_verificate"] = [OWNER, FIGLIO]
        destinatari = [
            {"id": OWNER, "email": "own@test.it"},
            {"id": FIGLIO, "email": "sospeso@test.it"},
        ]
        out = await svc.filtra_recapitabili(primary, destinatari)
        assert [d["id"] for d in out] == [OWNER]

    async def test_verifica_a_blocchi(self):
        primary = FakeClient(selects={"email_suppressions": []})
        destinatari = [{"id": f"u{i}", "email": f"u{i}@test.it"} for i in range(250)]
        primary.rpc_results["fn_email_verificate"] = [d["id"] for d in destinatari]
        out = await svc.filtra_recapitabili(primary, destinatari)
        assert len(out) == 250
        blocchi = [p["p_user_ids"] for fn, p in primary.rpc_calls if fn == "fn_email_verificate"]
        assert [len(b) for b in blocchi] == [100, 100, 50]
        assert sum(blocchi, []) == [d["id"] for d in destinatari]


class TestClaimLedger:
    def eleggibile(self) -> list:
        return [(candidato(), {"punteggio": 100})]

    async def test_nuova_coppia_claimata(self):
        primary = FakeClient(selects={"bando_alert_sends": []})
        claimed = await svc.claim_ledger(
            primary, OWNER, None, self.eleggibile(), oggi=OGGI, max_tentativi=3
        )
        assert claimed == {7: 101}
        # Legacy/non-Advisor: la riga nuova ha company_profile_id NULL e la
        # lettura filtra su NULL.
        select_op = next(
            op for op in primary.ops if op[0] == "bando_alert_sends" and op[1] == "select"
        )
        assert ("is", "company_profile_id", "null") in select_op[3]
        insert_op = next(
            op for op in primary.ops if op[0] == "bando_alert_sends" and op[1] == "upsert"
        )
        assert insert_op[2][0]["company_profile_id"] is None

    async def test_scope_azienda_nella_chiave(self):
        """Advisor: lettura e scrittura del ledger scopate su company_profile_id
        (la stessa coppia utente+bando può esistere per un'altra azienda)."""
        primary = FakeClient(selects={"bando_alert_sends": []})
        claimed = await svc.claim_ledger(
            primary, OWNER, COMPANY_ID, self.eleggibile(), oggi=OGGI, max_tentativi=3
        )
        assert claimed == {7: 101}
        select_op = next(
            op for op in primary.ops if op[0] == "bando_alert_sends" and op[1] == "select"
        )
        assert ("eq", "company_profile_id", COMPANY_ID) in select_op[3]
        insert_op = next(
            op for op in primary.ops if op[0] == "bando_alert_sends" and op[1] == "upsert"
        )
        assert insert_op[2][0]["company_profile_id"] == COMPANY_ID

    async def test_inviata_mai_ritentata(self):
        primary = FakeClient(
            selects={
                "bando_alert_sends": [
                    {"id": 1, "bando_id": 7, "stato": "inviata", "tentativi": 1}
                ]
            }
        )
        assert (
            await svc.claim_ledger(
                primary, OWNER, None, self.eleggibile(), oggi=OGGI, max_tentativi=3
            )
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
            primary, OWNER, None, self.eleggibile(), oggi=OGGI, max_tentativi=3
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
            await svc.claim_ledger(
                primary, OWNER, None, self.eleggibile(), oggi=OGGI, max_tentativi=3
            )
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


def primary_per_run_multi() -> FakeClient:
    """Advisor (piano max_aziende=10) con DUE aziende vive, entrambe compatibili
    con il bando di prova: il run fa fan-out per azienda."""
    primary = FakeClient(
        selects={
            "company_profiles": [
                {
                    "id": COMPANY_A,
                    "parent_id": OWNER,
                    "ragione_sociale": "Alfa Srl",
                    "ateco_id": 10,
                    "settore_id": None,
                    "regione_id": 1,
                    "beneficiari": [],
                },
                {
                    "id": COMPANY_B,
                    "parent_id": OWNER,
                    "ragione_sociale": "Beta Spa",
                    "ateco_id": 10,
                    "settore_id": None,
                    "regione_id": 1,
                    "beneficiari": [],
                },
            ],
            "company_data": [
                {"company_profile_id": COMPANY_A, "derived": None},
                {"company_profile_id": COMPANY_B, "derived": None},
            ],
            "user_subscriptions": [
                {
                    "user_id": OWNER,
                    "subscription_plans": {
                        "alert_attivo": True,
                        "alert_ritardo_giorni": 1,
                        "max_aziende": 10,
                    },
                }
            ],
            "family_members": [],
            "profiles": [{"id": OWNER, "email": "own@test.it", "is_active": True}],
            "email_suppressions": [],
            "bando_alert_settings": [
                {"user_id": OWNER, "abilitati": True, "unsubscribe_token": "tok-1"}
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
        secondary = FakeClient(selects={"bando_pubblico": [bando_row()]})
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
        secondary = FakeClient(selects={"bando_pubblico": [bando_row()]})
        riepilogo = await svc.esegui_run(primary, secondary, OGGI)
        assert riepilogo["esito"] == "ok"
        assert riepilogo["destinatari"] == 0
        assert email_calls == []

    async def test_invio_fallito_conteggiato(self, notify_calls, stub_lookups, monkeypatch):
        async def fake_send(*args, **kwargs):
            return False

        monkeypatch.setattr(svc.email_service, "send_bandi_digest_email", fake_send)
        primary = primary_per_run()
        secondary = FakeClient(selects={"bando_pubblico": [bando_row()]})
        riepilogo = await svc.esegui_run(primary, secondary, OGGI)
        assert riepilogo["esito"] == "ok"
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

    async def test_lookup_non_disponibili_run_in_errore_senza_invii(
        self, email_calls, notify_calls, monkeypatch
    ):
        # Senza i lookup (cache vuota, catalogo non leggibile) la run si
        # ferma come con l'errore del catalogo di prima: nessuna email con
        # facet e compatibilità calcolati su liste vuote.
        from app.core.errors import CatalogoNonDisponibileError

        async def non_disponibili(secondary):
            raise CatalogoNonDisponibileError()

        monkeypatch.setattr(svc.lookup_service, "get_lookups", non_disponibili)
        primary = primary_per_run()
        secondary = FakeClient(selects={"bando_pubblico": [bando_row()]})
        riepilogo = await svc.esegui_run(primary, secondary, OGGI)
        assert riepilogo["esito"] == "errore"
        assert riepilogo["bandi_candidati"] == 1
        assert "catalogo" in riepilogo["dettagli"]["errore"].lower()
        assert email_calls == [] and notify_calls == []


class TestCaricaCandidati:
    """Candidati dalla vista `bando_pubblico`, a pagine: PostgREST del catalogo
    non restituisce mai più di 1000 righe per richiesta (max-rows)."""

    async def _carica(self, secondary):
        return await svc.carica_candidati(
            secondary, oggi=OGGI, attivazione=date(2026, 7, 1), orizzonte_giorni=60, fuso=ROMA
        )

    @staticmethod
    def _righe(n: int) -> list[dict]:
        return [bando_row(id=i, slug=f"bando-{i}") for i in range(1, n + 1)]

    async def test_richiesta_sulla_vista(self):
        secondary = FakeClient(selects={"bando_pubblico": [bando_row()]})
        [c] = await self._carica(secondary)
        assert c.id == 7
        [(tabella, _, _, filtri)] = secondary.ops
        assert tabella == "bando_pubblico"
        assert ("eq", "stato_processing", "completed") not in filtri
        assert ("is", "slug", "null") in filtri
        assert ("order", ("id",), {}) in filtri
        assert ("range", 0, svc.PAGINA_CANDIDATI - 1) in filtri
        segmento, finestra = [f[1] for f in filtri if f[0] == "or"]
        assert segmento.startswith("stato_effettivo.in.(")
        assert finestra.startswith("data_pubblicazione.gte.")

    async def test_oltre_mille_righe_a_pagine(self):
        secondary = FakeClient(selects={"bando_pubblico": self._righe(2500)})
        candidati = await self._carica(secondary)
        assert len(candidati) == 2500
        assert [f for op in secondary.ops for f in op[3] if f[0] == "range"] == [
            ("range", 0, 999), ("range", 1000, 1999), ("range", 2000, 2999)
        ]

    async def test_esattamente_una_pagina_una_richiesta(self):
        secondary = FakeClient(selects={"bando_pubblico": self._righe(1000)})
        assert len(await self._carica(secondary)) == 1000
        assert len(secondary.ops) == 1

    async def test_max_rows_del_server_piu_basso(self):
        # Il conteggio esatto guida la paginazione anche se il server tronca
        # prima delle 1000 righe.
        secondary = FakeClient(selects={"bando_pubblico": self._righe(1200)})
        secondary.max_rows = 500
        candidati = await self._carica(secondary)
        assert len(candidati) == 1200
        assert len(secondary.ops) == 3

    async def test_doppioni_fra_pagine_scartati(self):
        secondary = FakeClient(selects={"bando_pubblico": []})
        prima = self._righe(1000)
        secondary.select_queues["bando_pubblico"] = [prima, [prima[-1]], []]
        candidati = await self._carica(secondary)
        assert len(candidati) == 1000
        assert len({c.id for c in candidati}) == 1000

    async def test_tetto_di_pagine_con_warning(self, monkeypatch, caplog):
        monkeypatch.setattr(svc, "MAX_PAGINE_CANDIDATI", 2)
        secondary = FakeClient(selects={"bando_pubblico": self._righe(2500)})
        with caplog.at_level(logging.WARNING, logger="bandofit.bando_alerts"):
            candidati = await self._carica(secondary)
        assert len(candidati) == 2000
        assert len(secondary.ops) == 2
        assert any("tetto" in r.getMessage() for r in caplog.records)

    async def test_tabella_sbagliata_fallisce(self):
        # Il fake non risponde su tabelle non previste: una lettura di
        # `bando` non passerebbe inosservata.
        with pytest.raises(AssertionError, match="tabella inattesa: bando_pubblico"):
            await self._carica(FakeClient(selects={"bando": [bando_row()]}))

    async def test_conteggio_calato_fra_due_pagine_chiude_la_lettura(self, caplog):
        # Fra la prima e la seconda pagina il segmento si è ristretto sotto le
        # righe già lette: il catalogo rifiuta l'intervallo (contratto §8) e
        # la lettura finisce con le righe della prima pagina, senza errore.
        prima = self._righe(1000)
        secondary = FakeClientConErrori(prima, errore_postgrest("PGRST103"))
        with caplog.at_level(logging.INFO, logger="bandofit.bando_alerts"):
            candidati = await self._carica(secondary)
        assert len(candidati) == 1000
        assert [f for op in secondary.ops for f in op[3] if f[0] == "range"] == [
            ("range", 0, 999), ("range", 1000, 1999)
        ]
        assert not any(r.levelno >= logging.WARNING for r in caplog.records)
        assert any("calati" in r.getMessage() for r in caplog.records)

    async def test_altro_errore_alla_seconda_pagina_si_propaga(self):
        secondary = FakeClientConErrori(self._righe(1000), errore_postgrest("57014"))
        with pytest.raises(APIError) as info:
            await self._carica(secondary)
        assert info.value.code == "57014"


class TestCaricaLimiti:
    async def test_override_vince_sul_piano(self):
        primary = FakeClient(
            selects={
                "profiles": [{"id": OWNER, "max_aziende_override": 3}],
                "user_subscriptions": [
                    {"user_id": OWNER, "subscription_plans": {"max_aziende": 10}}
                ],
            }
        )
        assert await svc.carica_limiti_aziende(primary, [OWNER]) == {OWNER: 3}

    async def test_piano_quando_niente_override(self):
        primary = FakeClient(
            selects={
                "profiles": [{"id": OWNER, "max_aziende_override": None}],
                "user_subscriptions": [
                    {"user_id": OWNER, "subscription_plans": {"max_aziende": 10}}
                ],
            }
        )
        assert await svc.carica_limiti_aziende(primary, [OWNER]) == {OWNER: 10}

    async def test_default_uno_senza_piano(self):
        primary = FakeClient(selects={"profiles": [], "user_subscriptions": []})
        assert await svc.carica_limiti_aziende(primary, [OWNER]) == {OWNER: 1}


class TestCaricaCompanyFacets:
    async def test_non_advisor_scope_nullo(self, stub_lookups):
        primary = primary_per_run()  # limite effettivo 1
        per_owner = await svc.carica_company_facets(primary, lookups())
        [azienda] = per_owner[OWNER]
        assert azienda.company_id == COMPANY_ID
        assert azienda.scope_value is None  # non-Advisor: ledger a NULL (parità)
        # Solo aziende vive: la query filtra deleted_at/archived_at.
        select_op = next(
            op for op in primary.ops if op[0] == "company_profiles" and op[1] == "select"
        )
        assert ("is", "deleted_at", "null") in select_op[3]
        assert ("is", "archived_at", "null") in select_op[3]

    async def test_advisor_scope_per_azienda(self, stub_lookups):
        primary = primary_per_run_multi()
        per_owner = await svc.carica_company_facets(primary, lookups())
        aziende = per_owner[OWNER]
        assert {a.company_id for a in aziende} == {COMPANY_A, COMPANY_B}
        assert all(a.scope_value == a.company_id for a in aziende)  # Advisor
        assert {a.ragione_sociale for a in aziende} == {"Alfa Srl", "Beta Spa"}


class TestEseguiRunMulti:
    async def test_fanout_per_azienda(self, email_multi_calls, notify_calls, stub_lookups):
        primary = primary_per_run_multi()
        secondary = FakeClient(selects={"bando_pubblico": [bando_row()]})
        riepilogo = await svc.esegui_run(primary, secondary, OGGI)

        assert riepilogo["esito"] == "ok"
        assert riepilogo["destinatari"] == 1
        assert riepilogo["email_inviate"] == 1

        # Una sola email, con una sezione per azienda.
        [email] = email_multi_calls
        assert email["to"] == "own@test.it"
        assert {s["azienda"] for s in email["sezioni"]} == {"Alfa Srl", "Beta Spa"}
        assert all(len(s["bandi"]) == 1 for s in email["sezioni"])

        # Due righe di ledger, una per azienda (company_profile_id distinti).
        insert_ops = [
            op for op in primary.ops if op[0] == "bando_alert_sends" and op[1] == "upsert"
        ]
        companies_inserite = {
            row["company_profile_id"] for op in insert_ops for row in op[2]
        }
        assert companies_inserite == {COMPANY_A, COMPANY_B}

        # Una notifica per azienda, con company_profile_id per il centro alert.
        assert len(notify_calls) == 2
        assert {n["company_profile_id"] for n in notify_calls} == {COMPANY_A, COMPANY_B}
        assert all(n["url"] == "/app/notifiche" for n in notify_calls)
        assert all(
            n["dedup_key"].startswith(f"bando-alert:{OGGI.isoformat()}:") for n in notify_calls
        )


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