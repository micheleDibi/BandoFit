"""Registro fatture interno: creazione della riga dal purchase pagato
(idempotente, data documento = incasso, snapshot obbligatorio) e recupero
delle righe mancanti. La creazione è INCONDIZIONATA: il modulo non legge
alcuna config emittente (l'emissione fiscale è fuori piattaforma)."""

import uuid
from types import SimpleNamespace

from app.services import invoice_service

PURCHASE = {
    "id": "p-1", "descrizione": "Abbonamento Pro (12 mesi)",
    "imponibile_cents": 29900, "iva_cents": 6578, "totale_cents": 36478,
    "iva_aliquota": "22.00", "natura_iva": None, "valuta": "EUR",
}


# --------------------------------------------------------------- fake primary


class FakeQuery:
    def __init__(self, fake, table):
        self.fake = fake
        self.table = table
        self.op = "select"
        self.payload = None
        self.cols = "*"
        self.filtri = []
        self.neq_filtri = []
        self.gt_filtri = []
        self.in_filtri = []

    def select(self, cols="*", **_k):
        self.cols = cols
        return self

    def insert(self, payload):
        self.op = "insert"
        self.payload = payload
        return self

    def eq(self, c, v):
        self.filtri.append((c, v))
        return self

    def neq(self, c, v):
        self.neq_filtri.append((c, v))
        return self

    def gt(self, c, v):
        self.gt_filtri.append((c, v))
        return self

    def in_(self, c, values):
        self.in_filtri.append((c, list(values)))
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a):
        return self

    def _match(self, row):
        for c, v in self.filtri:
            if str(row.get(c)) != str(v):
                return False
        for c, v in self.neq_filtri:
            if str(row.get(c)) == str(v):
                return False
        for c, v in self.gt_filtri:
            if not (row.get(c) is not None and row.get(c) > v):
                return False
        for c, values in self.in_filtri:
            if row.get(c) not in values:
                return False
        return True

    async def execute(self):
        righe = self.fake.righe.setdefault(self.table, [])
        if self.op == "insert":
            if self.table == "invoices" and any(
                r["purchase_id"] == self.payload["purchase_id"] for r in righe
            ):
                raise Exception('violates "invoices_purchase_id_key"')
            riga = {"id": str(uuid.uuid4()), **self.payload}
            righe.append(riga)
            return SimpleNamespace(data=[riga])
        # Come PostgREST: la risposta contiene SOLO le colonne selezionate —
        # un servizio che indicizza una colonna fuori select deve fallire QUI.
        trovate = [dict(r) for r in righe if self._match(r)]
        if self.cols != "*":
            chiavi = [c.strip() for c in self.cols.split(",")]
            trovate = [{k: r.get(k) for k in chiavi} for r in trovate]
        return SimpleNamespace(data=trovate)


class FakePrimary:
    def __init__(self, righe):
        self.righe = righe

    def table(self, name):
        return FakeQuery(self, name)


def _primary_con_fattura():
    return FakePrimary({
        "invoices": [{
            "id": "inv-1", "purchase_id": "p-1", "anno": 2027, "serie": "",
            "numero": None, "data_documento": "2027-03-01", "stato": "da_emettere",
            "provider_id": None, "imponibile_cents": 29900, "iva_cents": 6578,
            "totale_cents": 36478,
            "cliente_snapshot": {"tipo_soggetto": "azienda_it", "denominazione": "ACME",
                                 "partita_iva": "12345678901", "codice_destinatario": "ABC1234",
                                 "indirizzo": "V", "cap": "00100", "comune": "R", "provincia": "RM"},
            "tentativi": 0,
        }],
        "purchases": [dict(PURCHASE)],
    })


class TestCreazione:
    async def test_cambio_admin_non_genera_fattura(self):
        primary = FakePrimary({"invoices": []})
        out = await invoice_service.crea_fattura_da_purchase(
            primary, {"id": "p", "kind": "cambio_admin", "totale_cents": 0}
        )
        assert out is None

    async def test_data_documento_da_paid_at(self):
        primary = FakePrimary({"invoices": []})
        await invoice_service.crea_fattura_da_purchase(primary, {
            "id": "p-1", "kind": "piano", "totale_cents": 36478,
            "imponibile_cents": 29900, "iva_cents": 6578,
            "paid_at": "2027-07-31T22:00:00+00:00",  # UTC sera → 1/8 a Roma
            "billing_snapshot": {"tipo_soggetto": "azienda_it"},
        })
        inv = primary.righe["invoices"][0]
        assert inv["data_documento"] == "2027-08-01"  # convertito a Europe/Rome

    async def test_snapshot_vuoto_non_crea_fattura(self):
        # rete di sicurezza: mai una riga di registro senza cessionario
        primary = FakePrimary({"invoices": []})
        out = await invoice_service.crea_fattura_da_purchase(primary, {
            "id": "p-x", "kind": "rinnovo", "totale_cents": 36478,
            "imponibile_cents": 29900, "iva_cents": 6578,
            "paid_at": "2027-03-01T10:00:00+00:00", "billing_snapshot": {},
        })
        assert out is None
        assert primary.righe["invoices"] == []

    async def test_idempotente(self):
        primary = _primary_con_fattura()
        out = await invoice_service.crea_fattura_da_purchase(
            primary, {"id": "p-1", "kind": "piano", "totale_cents": 36478,
                      "imponibile_cents": 29900, "iva_cents": 6578,
                      "paid_at": "2027-03-01T10:00:00+00:00",
                      "billing_snapshot": {"tipo_soggetto": "azienda_it"}}
        )
        assert out is None  # già esiste


class TestRecupero:
    async def test_recupera_fattura_mancante_di_un_pagato(self):
        # purchase pagato SENZA riga fattura (il _crea_fattura best-effort era
        # fallito): il recupero la ricrea.
        primary = FakePrimary({
            "purchases": [{
                "id": "p-orfano", "kind": "piano", "status": "pagato",
                "totale_cents": 36478, "imponibile_cents": 29900, "iva_cents": 6578,
                "billing_snapshot": {"tipo_soggetto": "azienda_it"},
                "paid_at": "2027-03-01T10:00:00+00:00",
            }],
            "invoices": [],
        })
        creati = await invoice_service.recupera_fatture_mancanti(primary)
        assert creati == 1
        assert primary.righe["invoices"][0]["purchase_id"] == "p-orfano"

    async def test_non_ricrea_se_gia_fatturato(self):
        primary = _primary_con_fattura()
        primary.righe["purchases"][0].update({
            "id": "p-1", "status": "pagato", "kind": "piano",
            "billing_snapshot": {"tipo_soggetto": "azienda_it"},
            "paid_at": "2027-03-01T10:00:00+00:00",
        })
        assert await invoice_service.recupera_fatture_mancanti(primary) == 0
