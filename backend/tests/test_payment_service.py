"""payment_service: guardie del checkout, riconciliazione ordini (webhook e
sync condividono la stessa strada), anomalie mai mute."""

import uuid
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.schemas.payment import CheckoutIn, CheckoutTargetIn
from app.services import notification_service, payment_service

USER = "00000000-0000-0000-0000-000000000001"

PIANI = [
    {"id": 1, "slug": "gratuito", "nome": "Gratuito", "prezzo_annuale": "0.00",
     "ordering": 1, "tipo_prezzo": "gratis", "is_active": True},
    {"id": 2, "slug": "smart", "nome": "Smart", "prezzo_annuale": "99.00",
     "ordering": 2, "tipo_prezzo": "importo", "is_active": True},
    {"id": 3, "slug": "pro", "nome": "Pro", "prezzo_annuale": "299.00",
     "ordering": 3, "tipo_prezzo": "importo", "is_active": True},
]

BILLING_IT = {
    "user_id": USER, "tipo_soggetto": "azienda_it", "denominazione": "ACME Srl",
    "partita_iva": "03930330794", "paese": "IT", "indirizzo": "Via Roma 1",
    "comune": "Catanzaro", "provincia": "CZ", "cap": "88100",
    "codice_destinatario": "ABC1234",
}


class FakeQuery:
    def __init__(self, fake, table):
        self.fake = fake
        self.table = table
        self.op = "select"
        self.payload = None
        self.filtri = []
        self._count = None
        self._range = None

    def select(self, *_a, **kwargs):
        self.op = "select"
        self._count = kwargs.get("count")
        return self

    def insert(self, payload):
        self.op = "insert"
        self.payload = payload
        return self

    def update(self, payload):
        self.op = "update"
        self.payload = payload
        return self

    def upsert(self, payload, **_k):
        self.op = "upsert"
        self.payload = payload
        return self

    def eq(self, col, val):
        self.filtri.append((col, val))
        return self

    def gt(self, *_a):
        return self

    def order(self, *_a, **_k):
        return self

    def range(self, a, b):
        self._range = (a, b)
        return self

    def limit(self, *_a):
        return self

    def _match(self, row):
        return all(str(row.get(c)) == str(v) for c, v in self.filtri)

    async def execute(self):
        righe = self.fake.righe.setdefault(self.table, [])
        if self.op == "insert":
            if self.table == "purchases" and self.payload.get("status") == "in_attesa":
                if any(r.get("status") == "in_attesa"
                       and r.get("user_id") == self.payload["user_id"] for r in righe):
                    raise Exception('violates "purchases_one_pending"')
            riga = {"id": str(uuid.uuid4()), "created_at": "2026-07-17T10:00:00+00:00",
                    **self.payload}
            righe.append(riga)
            self.fake.inserts.append((self.table, riga))
            return SimpleNamespace(data=[riga], count=None)
        if self.op in ("update", "upsert"):
            trovate = [r for r in righe if self._match(r)] if self.op == "update" else []
            for r in trovate:
                r.update(self.payload)
            if self.op == "upsert":
                righe.append(dict(self.payload))
            self.fake.updates.append((self.table, self.payload, self.filtri))
            return SimpleNamespace(data=trovate or [self.payload], count=None)
        trovate = [r for r in righe if self._match(r)]
        if self._range:
            a, b = self._range
            pagina = trovate[a:b + 1]
        else:
            pagina = trovate
        return SimpleNamespace(data=pagina, count=len(trovate))


class FakeRpcCall:
    def __init__(self, fake, fn, params):
        self.fake = fake
        self.fn = fn
        self.params = params

    async def execute(self):
        self.fake.rpc_calls.append((self.fn, self.params))
        return SimpleNamespace(data=self.fake.rpc_result.get(self.fn, {"esito": "applicato"}))


class FakePrimary:
    def __init__(self, righe=None):
        self.righe = righe or {}
        self.inserts = []
        self.updates = []
        self.rpc_calls = []
        self.rpc_result = {}

    def table(self, name):
        return FakeQuery(self, name)

    def rpc(self, fn, params):
        return FakeRpcCall(self, fn, params)


class FakeRevolut:
    def __init__(self):
        self.enabled = True
        self.ordini = {}
        self.chiamate = []
        self.fail_create_order = False

    async def create_customer(self, email, full_name=None):
        self.chiamate.append(("create_customer", email))
        return {"id": "cust-1"}

    async def create_order(self, **kwargs):
        self.chiamate.append(("create_order", kwargs))
        if self.fail_create_order:
            raise RuntimeError("revolut giù")
        oid = f"ord-{len(self.ordini) + 1}"
        self.ordini[oid] = {"id": oid, "state": "pending", "payments": []}
        return {"id": oid, "token": "tok-1", "checkout_url": "https://pay/x"}

    async def get_order(self, order_id):
        return self.ordini[order_id]

    async def cancel_order(self, order_id):
        self.ordini[order_id]["state"] = "cancelled"
        return self.ordini[order_id]


def _primary(*, sub_plan="smart", giorni=183, billing=True, righe_extra=None):
    scadenza = (date.today() + timedelta(days=giorni)).isoformat()
    piano = next(p for p in PIANI if p["slug"] == sub_plan)
    righe = {
        "subscription_plans": [dict(p) for p in PIANI],
        "user_subscriptions": [{
            "id": "sub-1", "user_id": USER, "status": "active",
            "data_scadenza": scadenza, "auto_renew": False,
            "subscription_plans": dict(piano),
        }],
        "billing_profiles": [dict(BILLING_IT)] if billing else [],
        "revolut_customers": [],
        "purchases": [],
        "profiles": [{"id": "admin-1", "role": "admin"}],
        "audit_log": [],
    }
    righe.update(righe_extra or {})
    return FakePrimary(righe)


@pytest.fixture(autouse=True)
def _niente_notifiche(monkeypatch):
    inviate = []

    async def fake_notify(_primary, user_ids, **kwargs):
        inviate.append((user_ids, kwargs))

    monkeypatch.setattr(notification_service, "notify", fake_notify)
    return inviate


class TestPreview:
    async def test_upgrade_smart_pro(self):
        out = await payment_service.preview(
            _primary(), USER, CheckoutTargetIn(plan_slug="pro")
        )
        assert out.credito_cents == 4964          # 99×183/365 → 49,64
        assert out.imponibile_cents == 24936
        assert out.iva_cents == 5486              # 22% HALF_UP
        assert out.totale_cents == 30422
        assert out.dettaglio["giorni_residui"] == 183

    async def test_downgrade_rifiutato(self):
        with pytest.raises(BadRequestError, match="superiore"):
            await payment_service.preview(
                _primary(sub_plan="pro"), USER, CheckoutTargetIn(plan_slug="smart")
            )

    async def test_addon_permanente_gia_posseduto_rifiutato(self):
        # Un addon permanente già in inventario non si ricompra (409).
        primary = _primary(righe_extra={
            "addons": [{"id": 9, "slug": "report-pro", "nome": "Report Pro",
                        "prezzo": "20.00", "tipo_prezzo": "importo",
                        "tipo_fruizione": "permanente", "is_active": True}],
            "user_addon_inventory": [{"user_id": USER, "addon_id": 9, "quantita": 1}],
        })
        with pytest.raises(ConflictError, match="Possiedi già"):
            await payment_service.preview(
                primary, USER, CheckoutTargetIn(addon_slug="report-pro")
            )

    async def test_reverse_charge_ue(self):
        primary = _primary()
        primary.righe["billing_profiles"][0].update(
            {"tipo_soggetto": "azienda_ue", "paese": "DE"})
        out = await payment_service.preview(primary, USER, CheckoutTargetIn(plan_slug="pro"))
        assert out.iva_cents == 0 and out.natura_iva == "N2.1"
        assert out.totale_cents == out.imponibile_cents


class TestCheckout:
    async def test_senza_dati_di_fatturazione(self):
        with pytest.raises(BadRequestError, match="fatturazione"):
            await payment_service.checkout(
                _primary(billing=False), FakeRevolut(), USER, "u@x.it",
                CheckoutIn(plan_slug="pro"),
            )

    async def test_flusso_felice_congela_tutto(self):
        primary = _primary()
        revolut = FakeRevolut()
        out = await payment_service.checkout(
            primary, revolut, USER, "u@x.it", CheckoutIn(plan_slug="pro", auto_renew=True)
        )
        purchase = primary.righe["purchases"][0]
        assert purchase["billing_snapshot"]["partita_iva"] == "03930330794"
        assert purchase["auto_renew_scelto"] is True
        assert purchase["totale_cents"] == 30422
        ordine = revolut.chiamate[-1][1]
        assert ordine["amount_cents"] == 30422
        assert ordine["metadata"] == {"purchase_id": out.purchase_id}
        assert ordine["expire_pending_after"] == "PT1H"
        assert purchase["revolut_order_id"] == "ord-1"
        assert out.revolut_order_token == "tok-1"

    async def test_un_solo_checkout_in_corso(self):
        primary = _primary(righe_extra={"purchases": [
            {"id": "p0", "user_id": USER, "status": "in_attesa"}]})
        with pytest.raises(ConflictError, match="in corso"):
            await payment_service.checkout(
                primary, FakeRevolut(), USER, "u@x.it", CheckoutIn(plan_slug="pro")
            )

    async def test_ordine_non_creato_annulla_il_purchase(self):
        primary = _primary()
        revolut = FakeRevolut()
        revolut.fail_create_order = True
        with pytest.raises(RuntimeError):
            await payment_service.checkout(
                primary, revolut, USER, "u@x.it", CheckoutIn(plan_slug="pro")
            )
        fn, params = primary.rpc_calls[-1]
        assert fn == "fn_fail_purchase" and params["p_status"] == "annullato"


class TestElaboraOrdine:
    def _con_ordine(self, stato, payments=None, purchase_status="in_attesa"):
        primary = _primary(righe_extra={"purchases": [{
            "id": "p1", "user_id": USER, "kind": "piano", "status": purchase_status,
            "oggetto_slug": "pro", "oggetto_nome": "Pro", "descrizione": "d",
            "imponibile_cents": 1, "iva_cents": 0, "totale_cents": 1,
            "iva_aliquota": "22.00", "natura_iva": None, "valuta": "EUR",
            "decline_reason": None, "motivazione": None,
            "created_at": "2026-07-17T10:00:00+00:00", "paid_at": None,
            "revolut_order_id": "ord-1", "plan_id": 3, "addon_id": None,
        }]})
        revolut = FakeRevolut()
        revolut.ordini["ord-1"] = {"id": "ord-1", "state": stato,
                                   "payments": payments or []}
        return primary, revolut

    def test_select_include_user_id(self):
        # Regressione: _invia_ricevuta legge purchase["user_id"]; senza questo
        # campo nella query il completamento sollevava KeyError e mandava in
        # 500 il /sync (e in 'errore' il webhook).
        assert "user_id" in payment_service._PURCHASE_SELECT.split(",")

    async def test_completed_completa_con_il_payment_id(self):
        primary, revolut = self._con_ordine(
            "completed", [{"id": "pay-9", "state": "captured"}])
        esito = await payment_service.elabora_ordine(primary, revolut, "ord-1")
        assert esito == {"esito": "applicato"}
        fn, params = primary.rpc_calls[0]
        assert fn == "fn_complete_purchase"
        assert params == {"p_purchase_id": "p1", "p_revolut_payment_id": "pay-9"}

    async def test_side_effect_che_esplode_non_propaga(self, monkeypatch):
        # Il pagamento è già applicato dalla RPC: una ricevuta/fattura che
        # fallisce non deve far fallire la chiamata (né marcare il webhook
        # 'errore').
        async def esplode(*_a, **_k):
            raise RuntimeError("smtp giù")

        monkeypatch.setattr(payment_service, "_invia_ricevuta", esplode)
        primary, revolut = self._con_ordine(
            "completed", [{"id": "pay-9", "state": "captured"}])
        esito = await payment_service.elabora_ordine(primary, revolut, "ord-1")
        assert esito == {"esito": "applicato"}  # completato lo stesso

    async def test_orfano_segnalato_agli_admin(self, _niente_notifiche):
        primary, revolut = self._con_ordine(
            "completed", [{"id": "pay-9", "state": "captured"}])
        primary.rpc_result["fn_complete_purchase"] = {
            "esito": "pagamento_orfano", "stato_purchase": "annullato"}
        await payment_service.elabora_ordine(primary, revolut, "ord-1")
        assert any(t == "audit_log" for t, _ in primary.inserts)
        assert _niente_notifiche and _niente_notifiche[0][0] == ["admin-1"]
        assert _niente_notifiche[0][1]["tipo"] == "pagamento_orfano"

    async def test_ordine_fallito_scade_il_purchase(self):
        primary, revolut = self._con_ordine("failed")
        await payment_service.elabora_ordine(primary, revolut, "ord-1")
        fn, params = primary.rpc_calls[0]
        assert fn == "fn_fail_purchase" and params["p_status"] == "scaduto"

    async def test_pending_con_declino_aggiorna_solo_il_motivo(self):
        primary, revolut = self._con_ordine(
            "pending", [{"id": "pay-1", "state": "declined",
                         "decline_reason": "insufficient_funds"}])
        esito = await payment_service.elabora_ordine(primary, revolut, "ord-1")
        assert esito["esito"] == "in_corso"
        assert primary.rpc_calls == []  # nessuna transizione: si può ritentare
        assert primary.updates[-1][1] == {"decline_reason": "insufficient_funds"}

    async def test_incasso_senza_purchase_e_anomalia(self, _niente_notifiche):
        primary = _primary()
        revolut = FakeRevolut()
        revolut.ordini["ord-x"] = {"id": "ord-x", "state": "completed", "payments": []}
        esito = await payment_service.elabora_ordine(primary, revolut, "ord-x")
        assert esito["esito"] == "purchase_inesistente"
        assert _niente_notifiche  # mai un no-op muto


class TestStorico:
    async def test_sync_di_un_acquisto_altrui(self):
        primary = _primary(righe_extra={"purchases": [{
            "id": "p1", "user_id": "altro-utente", "status": "in_attesa"}]})
        with pytest.raises(NotFoundError):
            await payment_service.sync_purchase(primary, FakeRevolut(), USER, "p1")
