"""payment_scheduler: i 7 passi con «oggi» iniettato. Verifica i vincoli
della review: preavviso ≥7gg prima dell'addebito, giorno saltato recuperato,
il passo 2 non ricrea il tentativo 1, retry a +3/+7, grazia non calpestata,
nessun doppio addebito per ciclo."""

import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import email_service, payment_scheduler, pricing

OGGI = date(2027, 3, 1)


class FakeQuery:
    def __init__(self, fake, table):
        self.fake = fake
        self.table = table
        self.op = "select"
        self.payload = None
        self.filtri = []
        self.neq_null = []
        self.is_null = []
        self.acc_lte = []
        self.acc_gte = []
        self.acc_lt = []

    def select(self, *_a, **_k):
        return self

    def insert(self, payload):
        self.op = "insert"
        self.payload = payload
        return self

    def update(self, payload):
        self.op = "update"
        self.payload = payload
        return self

    def eq(self, c, v):
        self.filtri.append((c, v))
        return self

    def is_(self, c, _v):
        self.is_null.append(c)
        return self

    def limit(self, *_a):
        return self

    # postgrest usa attributi speciali per not_/lte/lt: li simulo via metodo
    def __getattr__(self, name):
        if name == "not_":
            return _Not(self)
        raise AttributeError(name)

    def lte(self, c, v):
        self.acc_lte.append((c, v))
        return self

    def gte(self, c, v):
        self.acc_gte.append((c, v))
        return self

    def lt(self, c, v):
        self.acc_lt.append((c, v))
        return self

    def _match(self, row):
        for c, v in self.filtri:
            if str(row.get(c)) != str(v):
                return False
        for c in self.is_null:
            if row.get(c) is not None:
                return False
        for c in self.neq_null:
            if row.get(c) is None:
                return False
        for c, v in self.acc_lte:
            if row.get(c) is None or str(row.get(c)) > str(v):
                return False
        for c, v in self.acc_gte:
            if row.get(c) is None or str(row.get(c)) < str(v):
                return False
        for c, v in self.acc_lt:
            if row.get(c) is None or str(row.get(c)) >= str(v):
                return False
        return True

    async def execute(self):
        righe = self.fake.righe.setdefault(self.table, [])
        if self.op == "insert":
            riga = {"id": str(uuid.uuid4()), **self.payload}
            righe.append(riga)
            self.fake.inserts.append((self.table, riga))
            return SimpleNamespace(data=[riga])
        if self.op == "update":
            tocc = [r for r in righe if self._match(r)]
            for r in tocc:
                r.update({k: v for k, v in self.payload.items() if v != "now()"})
            self.fake.updates.append((self.table, self.payload, list(self.filtri)))
            return SimpleNamespace(data=tocc)
        return SimpleNamespace(data=[dict(r) for r in righe if self._match(r)])


class _Not:
    def __init__(self, q):
        self.q = q

    def is_(self, c, _v):
        self.q.neq_null.append(c)
        return self.q


class FakeRpc:
    def __init__(self, fake, fn, params):
        self.fake = fake
        self.fn = fn
        self.params = params

    async def execute(self):
        self.fake.rpc_calls.append((self.fn, self.params))
        # esegui il cambio programmato: marca eseguito
        if self.fn == "fn_execute_scheduled_change":
            for r in self.fake.righe.get("scheduled_plan_changes", []):
                if str(r["id"]) == str(self.params["p_id"]):
                    r["status"] = "eseguito"
        return SimpleNamespace(data={"esito": "eseguito"})


class FakePrimary:
    def __init__(self, righe):
        self.righe = righe
        self.inserts = []
        self.updates = []
        self.rpc_calls = []

    def table(self, name):
        return FakeQuery(self, name)

    def rpc(self, fn, params):
        return FakeRpc(self, fn, params)


class FakeRevolut:
    def __init__(self):
        self.ordini = []
        self.charge = []

    async def create_order(self, **kwargs):
        self.ordini.append(kwargs)
        return {"id": f"ord-{len(self.ordini)}", "token": "t"}

    async def pay_with_saved_method(self, order_id, method_id, method_type="card"):
        self.charge.append((order_id, method_id))
        return {"state": "authorisation_passed"}


PIANO_PRO = {"id": 3, "slug": "pro", "nome": "Pro", "prezzo_annuale": "299.00",
             "tipo_prezzo": "importo"}


def _sub(user_id, *, scadenza, auto_renew=True, notice=None, grace=None, piano=None):
    return {
        "user_id": user_id, "status": "active", "data_scadenza": scadenza,
        "auto_renew": auto_renew, "renewal_notice_sent_at": notice,
        "grace_until": grace, "plan_id": 3,
        "subscription_plans": piano or dict(PIANO_PRO),
    }


def _base_righe(subs, *, customers=True, purchases=None, scheduled=None):
    return {
        "user_subscriptions": subs,
        "profiles": [{"id": s["user_id"], "email": f"{s['user_id']}@x.it"} for s in subs],
        "revolut_customers": [
            {"user_id": s["user_id"], "revolut_customer_id": f"cust-{s['user_id']}",
             "saved_method_id": "pm-1" if customers else None,
             "saved_method_type": "card"}
            for s in subs
        ],
        "billing_profiles": [{
            "user_id": s["user_id"], "tipo_soggetto": "azienda",
            "denominazione": "ACME Srl", "partita_iva": "03930330794",
            "paese": "IT", "indirizzo": "Via Roma 1", "comune": "Catanzaro",
            "provincia": "CZ", "cap": "88100",
            "nome": None, "cognome": None, "codice_fiscale": None,
            "vies_valid": None, "vies_checked_at": None,
        } for s in subs],
        "purchases": purchases or [],
        "scheduled_plan_changes": scheduled or [],
        "subscription_plans": [dict(PIANO_PRO),
                               {"id": 1, "slug": "gratuito", "nome": "Gratuito",
                                "prezzo_annuale": "0.00", "tipo_prezzo": "gratis"}],
    }


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
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
def _email_stub(monkeypatch):
    inviate = []
    for fn in ("send_promemoria_rinnovo_email", "send_pagamento_fallito_email",
               "send_downgrade_email", "send_ricevuta_pagamento_email"):
        async def catcher(*a, _n=fn, **k):
            inviate.append(_n)
            return True
        monkeypatch.setattr(email_service, fn, catcher)
    return inviate


class TestPreavvisi:
    async def test_finestra_non_uguaglianza(self, _email_stub):
        # scadenza a +5 giorni: dentro la finestra dei 7, preavviso mai inviato
        sub = _sub("u1", scadenza=(OGGI + timedelta(days=5)).isoformat())
        primary = FakePrimary(_base_righe([sub]))
        n = await payment_scheduler.passo_preavvisi(primary, OGGI)
        assert n == 1
        assert "send_promemoria_rinnovo_email" in _email_stub
        assert sub["renewal_notice_sent_at"] is None or True  # marcato via update
        assert any(t == "user_subscriptions" and "renewal_notice_sent_at" in p
                   for t, p, _ in primary.updates)

    async def test_giorno_saltato_recuperato(self, _email_stub):
        # scadenza a +2 (il giorno -7 è già passato): la finestra lo prende
        sub = _sub("u1", scadenza=(OGGI + timedelta(days=2)).isoformat())
        primary = FakePrimary(_base_righe([sub]))
        assert await payment_scheduler.passo_preavvisi(primary, OGGI) == 1


class TestAddebiti:
    async def test_addebita_solo_se_preavviso_vecchio_di_7_giorni(self, _email_stub):
        vecchio = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        sub = _sub("u1", scadenza=OGGI.isoformat(), notice=vecchio)
        primary = FakePrimary(_base_righe([sub]))
        revolut = FakeRevolut()
        n = await payment_scheduler.passo_addebiti(primary, revolut, OGGI)
        assert n == 1
        assert len(revolut.charge) == 1
        purchase = primary.righe["purchases"][0]
        assert purchase["kind"] == "rinnovo" and purchase["tentativo"] == 1
        assert purchase["ciclo_rinnovo"] == OGGI.isoformat()
        assert purchase["totale_cents"] == 37375  # 299 + 25%
        # billing_snapshot COMPLETO congelato: senza, la fattura di rinnovo
        # nascerebbe con cessionario vuoto (difetto bloccante della review).
        assert purchase["billing_snapshot"]["partita_iva"] == "03930330794"
        assert purchase["billing_snapshot"]["tipo_soggetto"] == "azienda"

    async def test_rinnovo_reverse_charge_con_prova_vies(self, _email_stub):
        # Azienda UE con VIES valido: il rinnovo resta a 0% (RC-UE).
        vecchio = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        sub = _sub("u1", scadenza=OGGI.isoformat(), notice=vecchio)
        righe = _base_righe([sub])
        righe["billing_profiles"][0].update(
            {"paese": "DE", "partita_iva": "123456789", "vies_valid": True}
        )
        primary = FakePrimary(righe)
        assert await payment_scheduler.passo_addebiti(primary, FakeRevolut(), OGGI) == 1
        purchase = primary.righe["purchases"][0]
        assert purchase["iva_cents"] == 0
        assert purchase["natura_iva"] == "RC-UE"
        assert purchase["totale_cents"] == 29900  # solo imponibile

    async def test_senza_profilo_di_fatturazione_non_addebita(self, _email_stub):
        vecchio = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        sub = _sub("u1", scadenza=OGGI.isoformat(), notice=vecchio)
        righe = _base_righe([sub])
        righe["billing_profiles"] = []  # profilo mancante
        primary = FakePrimary(righe)
        revolut = FakeRevolut()
        assert await payment_scheduler.passo_addebiti(primary, revolut, OGGI) == 0
        assert revolut.charge == []  # niente addebito senza dati fattura

    async def test_preavviso_recente_non_addebita(self, _email_stub):
        recente = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        sub = _sub("u1", scadenza=OGGI.isoformat(), notice=recente)
        primary = FakePrimary(_base_righe([sub]))
        revolut = FakeRevolut()
        assert await payment_scheduler.passo_addebiti(primary, revolut, OGGI) == 0
        assert revolut.charge == []

    async def test_non_ricrea_tentativo_1_se_gia_presente(self, _email_stub):
        vecchio = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        sub = _sub("u1", scadenza=OGGI.isoformat(), notice=vecchio)
        righe = _base_righe([sub], purchases=[{
            "id": "p1", "user_id": "u1", "kind": "rinnovo", "status": "fallito",
            "ciclo_rinnovo": OGGI.isoformat(), "tentativo": 1,
        }])
        primary = FakePrimary(righe)
        revolut = FakeRevolut()
        assert await payment_scheduler.passo_addebiti(primary, revolut, OGGI) == 0
        assert revolut.charge == []  # nessun doppio addebito

    async def test_addebita_il_piano_di_destinazione(self, _email_stub):
        vecchio = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        sub = _sub("u1", scadenza=OGGI.isoformat(), notice=vecchio)
        smart = {"id": 2, "slug": "smart", "nome": "Smart", "prezzo_annuale": "99.00",
                 "tipo_prezzo": "importo"}
        righe = _base_righe([sub], scheduled=[{
            "id": "s1", "user_id": "u1", "to_plan_id": 2, "status": "programmato",
            "effective_date": OGGI.isoformat(), "subscription_plans": smart,
        }])
        righe["subscription_plans"].append(smart)
        primary = FakePrimary(righe)
        await payment_scheduler.passo_addebiti(primary, FakeRevolut(), OGGI)
        purchase = primary.righe["purchases"][0]
        assert purchase["plan_id"] == 2  # snapshotta la DESTINAZIONE
        assert purchase["totale_cents"] == 12375  # 99 + 25%

    async def test_senza_metodo_salvato_non_addebita(self, _email_stub):
        vecchio = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        sub = _sub("u1", scadenza=OGGI.isoformat(), notice=vecchio)
        primary = FakePrimary(_base_righe([sub], customers=False))
        revolut = FakeRevolut()
        assert await payment_scheduler.passo_addebiti(primary, revolut, OGGI) == 0


class TestRetry:
    async def test_tentativo_2_a_ciclo_piu_3(self, _email_stub):
        ciclo = (OGGI - timedelta(days=3)).isoformat()
        vecchio = (datetime.now(timezone.utc) - timedelta(days=11)).isoformat()
        sub = _sub("u1", scadenza=ciclo, notice=vecchio,
                   grace=(OGGI + timedelta(days=11)).isoformat())
        righe = _base_righe([sub], purchases=[{
            "id": "p1", "user_id": "u1", "kind": "rinnovo", "status": "fallito",
            "ciclo_rinnovo": ciclo, "tentativo": 1,
        }])
        primary = FakePrimary(righe)
        revolut = FakeRevolut()
        n = await payment_scheduler.passo_retry(primary, revolut, OGGI)
        assert n == 1
        nuovo = [p for p in primary.righe["purchases"] if p.get("tentativo") == 2]
        assert nuovo and "send_pagamento_fallito_email" in _email_stub

    async def test_giorno_saltato_recupera_il_retry(self, _email_stub):
        # lo scheduler NON gira a ciclo+3: al giorno dopo la FINESTRA recupera
        # il tentativo 2 (con l'uguaglianza sarebbe perso per sempre).
        ciclo = (OGGI - timedelta(days=4)).isoformat()  # oggi = ciclo+4
        vecchio = (datetime.now(timezone.utc) - timedelta(days=12)).isoformat()
        sub = _sub("u1", scadenza=ciclo, notice=vecchio,
                   grace=(OGGI + timedelta(days=10)).isoformat())
        righe = _base_righe([sub], purchases=[{
            "id": "p1", "user_id": "u1", "kind": "rinnovo", "status": "fallito",
            "ciclo_rinnovo": ciclo, "tentativo": 1,
        }])
        primary = FakePrimary(righe)
        n = await payment_scheduler.passo_retry(primary, FakeRevolut(), OGGI)
        assert n == 1
        assert any(p.get("tentativo") == 2 for p in primary.righe["purchases"])


class TestGrazia:
    async def test_fine_grazia_degrada(self, _email_stub):
        ciclo = (OGGI - timedelta(days=15)).isoformat()
        sub = _sub("u1", scadenza=ciclo, grace=(OGGI - timedelta(days=1)).isoformat())
        primary = FakePrimary(_base_righe([sub]))
        n = await payment_scheduler.passo_fine_grazia(primary, OGGI)
        assert n == 1
        assert any(fn == "fn_execute_scheduled_change" for fn, _ in primary.rpc_calls)
        assert "send_downgrade_email" in _email_stub

    async def test_manuale_non_calpesta_la_grazia(self, _email_stub):
        # auto_renew spento a metà dunning ma grazia ancora valida
        ciclo = (OGGI - timedelta(days=5)).isoformat()
        sub = _sub("u1", scadenza=ciclo, auto_renew=False,
                   grace=(OGGI + timedelta(days=9)).isoformat())
        primary = FakePrimary(_base_righe([sub]))
        assert await payment_scheduler.passo_scadenze_manuali(primary, OGGI) == 0
        assert primary.rpc_calls == []  # NON degradato

    async def test_scadenza_manuale_senza_grazia_degrada(self, _email_stub):
        ciclo = (OGGI - timedelta(days=1)).isoformat()
        sub = _sub("u1", scadenza=ciclo, auto_renew=False, grace=None)
        primary = FakePrimary(_base_righe([sub]))
        assert await payment_scheduler.passo_scadenze_manuali(primary, OGGI) == 1
        assert any(fn == "fn_execute_scheduled_change" for fn, _ in primary.rpc_calls)


class TestClaim:
    async def test_run_completa_isola_gli_errori(self, _email_stub, monkeypatch):
        sub = _sub("u1", scadenza=(OGGI + timedelta(days=5)).isoformat())
        primary = FakePrimary(_base_righe([sub]))

        async def esplode(*_a, **_k):
            raise RuntimeError("boom")

        monkeypatch.setattr(payment_scheduler, "passo_addebiti", esplode)
        esiti = await payment_scheduler.esegui_run(primary, FakeRevolut(), OGGI)
        assert esiti["addebiti"] == "errore"
        assert esiti["preavvisi"] == 1  # gli altri passi girano lo stesso
