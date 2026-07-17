"""Fatturazione SDI: builder FatturaPA per tipo di soggetto (B2B/B2C/UE),
protocollo di invio (numero congelato, esito ignoto riconciliato, scarto
ritrasmesso), data documento = incasso."""

import uuid
from datetime import date
from types import SimpleNamespace

import pytest

from app.clients.openapi import OpenapiTimeoutError
from app.services import fattura_builder, invoice_service

SETTINGS = SimpleNamespace(
    fattura_denominazione="BandoFit Srl", fattura_partita_iva="03930330794",
    fattura_codice_fiscale="03930330794", fattura_regime="RF01",
    fattura_sede_indirizzo="Via Roma 1", fattura_sede_comune="Catanzaro",
    fattura_sede_provincia="CZ", fattura_sede_cap="88100", fattura_serie="",
)

PURCHASE = {
    "id": "p-1", "descrizione": "Abbonamento Pro (12 mesi)",
    "imponibile_cents": 29900, "iva_cents": 6578, "totale_cents": 36478,
    "iva_aliquota": "22.00", "natura_iva": None, "valuta": "EUR",
}


# ------------------------------------------------------------------- builder


class TestBuilder:
    def _header(self, doc):
        return doc["FatturaElettronicaHeader"]

    def test_b2b_italia_codice_destinatario(self):
        cliente = {"tipo_soggetto": "azienda_it", "denominazione": "ACME Srl",
                   "partita_iva": "12345678901", "codice_destinatario": "ABC1234",
                   "indirizzo": "Via X 1", "cap": "00100", "comune": "Roma",
                   "provincia": "RM"}
        doc = fattura_builder.costruisci_fattura(
            settings=SETTINGS, purchase=PURCHASE, cliente=cliente,
            numero=1, serie="", data_documento="2027-03-01",
        )
        assert self._header(doc)["DatiTrasmissione"]["CodiceDestinatario"] == "ABC1234"
        assert doc["external_reference"] == "p-1"

    def test_b2b_italia_pec_fallback(self):
        cliente = {"tipo_soggetto": "azienda_it", "denominazione": "ACME Srl",
                   "partita_iva": "12345678901", "codice_destinatario": "0000000",
                   "pec": "acme@pec.it", "indirizzo": "Via X 1", "cap": "00100",
                   "comune": "Roma", "provincia": "RM"}
        doc = fattura_builder.costruisci_fattura(
            settings=SETTINGS, purchase=PURCHASE, cliente=cliente,
            numero=1, serie="", data_documento="2027-03-01",
        )
        dt = self._header(doc)["DatiTrasmissione"]
        assert dt["CodiceDestinatario"] == "0000000"
        assert dt["PECDestinatario"] == "acme@pec.it"

    def test_b2c_italia_zeri_e_cf(self):
        cliente = {"tipo_soggetto": "privato_it", "nome": "Anna", "cognome": "Bianchi",
                   "codice_fiscale": "BNCNNA80A41H501Z", "indirizzo": "Via Y 2",
                   "cap": "20100", "comune": "Milano", "provincia": "MI"}
        doc = fattura_builder.costruisci_fattura(
            settings=SETTINGS, purchase=PURCHASE, cliente=cliente,
            numero=5, serie="", data_documento="2027-03-01",
        )
        assert self._header(doc)["DatiTrasmissione"]["CodiceDestinatario"] == "0000000"
        anagrafica = self._header(doc)["CessionarioCommittente"]["DatiAnagrafici"]
        assert anagrafica["CodiceFiscale"] == "BNCNNA80A41H501Z"

    def test_ue_xxxxxxx_e_reverse_charge(self):
        cliente = {"tipo_soggetto": "azienda_ue", "denominazione": "Muster GmbH",
                   "paese": "DE", "partita_iva": "DE123456789",
                   "indirizzo": "Strasse 1", "comune": "Berlin"}
        ue_purchase = {**PURCHASE, "iva_cents": 0, "iva_aliquota": "0.00",
                       "natura_iva": "N2.1", "totale_cents": 29900}
        doc = fattura_builder.costruisci_fattura(
            settings=SETTINGS, purchase=ue_purchase, cliente=cliente,
            numero=7, serie="", data_documento="2027-03-01",
        )
        dt = self._header(doc)["DatiTrasmissione"]
        assert dt["CodiceDestinatario"] == "XXXXXXX"
        sede = self._header(doc)["CessionarioCommittente"]["Sede"]
        assert (sede["Nazione"], sede["CAP"], sede["Provincia"]) == ("DE", "00000", "EE")
        riepilogo = doc["FatturaElettronicaBody"]["DatiBeniServizi"]["DatiRiepilogo"][0]
        assert riepilogo["Natura"] == "N2.1" and riepilogo["Imposta"] == "0.00"

    def test_data_documento_e_quella_passata(self):
        cliente = {"tipo_soggetto": "privato_it", "nome": "A", "cognome": "B",
                   "codice_fiscale": "X", "indirizzo": "Y", "cap": "00100",
                   "comune": "Roma", "provincia": "RM"}
        doc = fattura_builder.costruisci_fattura(
            settings=SETTINGS, purchase=PURCHASE, cliente=cliente,
            numero=1, serie="", data_documento="2027-07-31",
        )
        assert doc["FatturaElettronicaBody"]["DatiGenerali"][
            "DatiGeneraliDocumento"]["Data"] == "2027-07-31"


# --------------------------------------------------------------- fake primary


class FakeQuery:
    def __init__(self, fake, table):
        self.fake = fake
        self.table = table
        self.op = "select"
        self.payload = None
        self.filtri = []
        self.neq_filtri = []
        self.gt_filtri = []
        self.in_filtri = []
        self.lte_filtri = []

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

    def lte(self, c, v):
        self.lte_filtri.append((c, v))
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
        for c, v in self.lte_filtri:
            if row.get(c) is None or str(row.get(c)) > str(v):
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
        if self.op == "update":
            tocc = [r for r in righe if self._match(r)]
            for r in tocc:
                r.update({k: v for k, v in self.payload.items() if v != "now()"})
            self.fake.updates.append((self.table, self.payload))
            return SimpleNamespace(data=tocc)
        return SimpleNamespace(data=[dict(r) for r in righe if self._match(r)])


class FakeRpc:
    def __init__(self, fake, fn, params):
        self.fake = fake
        self.fn = fn
        self.params = params

    async def execute(self):
        self.fake.counter += 1
        return SimpleNamespace(data=self.fake.counter)


class FakePrimary:
    def __init__(self, righe):
        self.righe = righe
        self.updates = []
        self.counter = 0

    def table(self, name):
        return FakeQuery(self, name)

    def rpc(self, fn, params):
        return FakeRpc(self, fn, params)


class FakeOpenapi:
    def __init__(self, *, enabled=True, esito=None, invia=None, cerca=None):
        self.enabled = enabled
        self._esito = esito or {"id": "prov-1", "stato": "sent"}
        self._invia = invia
        self._cerca = cerca
        self.inviate = []
        self.cerche = []

    async def invia_fattura(self, doc):
        self.inviate.append(doc)
        if self._invia:
            raise self._invia
        return self._esito

    async def cerca_fattura(self, rif):
        self.cerche.append(rif)
        return self._cerca


@pytest.fixture(autouse=True)
def _emittente(monkeypatch):
    monkeypatch.setattr(invoice_service, "get_settings", lambda: SETTINGS)


def _primary_con_fattura(stato="da_emettere", tentativi=0, provider_id=None, numero=None):
    return FakePrimary({
        "invoices": [{
            "id": "inv-1", "purchase_id": "p-1", "anno": 2027, "serie": "",
            "numero": numero, "data_documento": "2027-03-01", "stato": stato,
            "provider_id": provider_id, "imponibile_cents": 29900, "iva_cents": 6578,
            "totale_cents": 36478,
            "cliente_snapshot": {"tipo_soggetto": "azienda_it", "denominazione": "ACME",
                                 "partita_iva": "12345678901", "codice_destinatario": "ABC1234",
                                 "indirizzo": "V", "cap": "00100", "comune": "R", "provincia": "RM"},
            "tentativi": tentativi,
        }],
        "purchases": [dict(PURCHASE)],
    })


class TestEmissione:
    async def test_assegna_numero_e_invia(self):
        primary = _primary_con_fattura()
        openapi = FakeOpenapi()
        inv = primary.righe["invoices"][0]
        stato = await invoice_service.emetti(primary, openapi, inv)
        assert stato == "inviata"
        aggiornata = primary.righe["invoices"][0]
        assert aggiornata["numero"] == 1  # dal contatore
        assert aggiornata["provider_id"] == "prov-1"
        assert openapi.inviate[0]["external_reference"] == "p-1"

    async def test_esito_ignoto_non_ritrasmette_subito(self):
        primary = _primary_con_fattura()
        openapi = FakeOpenapi(invia=OpenapiTimeoutError())
        inv = primary.righe["invoices"][0]
        stato = await invoice_service.emetti(primary, openapi, inv)
        assert stato == "errore"
        assert primary.righe["invoices"][0]["tentativi"] == 1

    async def test_riconcilia_esito_ignoto_prima_di_reinviare(self):
        # seconda passata: tentativi>0, provider_id assente → cerca_fattura
        primary = _primary_con_fattura(stato="errore", tentativi=1, numero=1)
        openapi = FakeOpenapi(cerca={"id": "prov-9", "stato": "delivered"})
        inv = primary.righe["invoices"][0]
        stato = await invoice_service.emetti(primary, openapi, inv)
        assert openapi.cerche == ["p-1"]
        assert openapi.inviate == []  # NON ritrasmessa: trovata già a SDI
        assert stato == "inviata"
        assert primary.righe["invoices"][0]["provider_id"] == "prov-9"

    async def test_scarto_ritrasmette_stesso_numero(self):
        primary = _primary_con_fattura(stato="scartata", numero=42)
        openapi = FakeOpenapi(esito={"id": "prov-2", "stato": "sent"})
        inv = primary.righe["invoices"][0]
        await invoice_service.emetti(primary, openapi, inv)
        # numero già assegnato: non ne chiede uno nuovo
        assert primary.counter == 0
        assert primary.righe["invoices"][0]["numero"] == 42

    async def test_emittente_non_configurato_no_op(self, monkeypatch):
        monkeypatch.setattr(invoice_service, "get_settings",
                            lambda: SimpleNamespace(fattura_denominazione="", fattura_partita_iva=""))
        primary = _primary_con_fattura()
        inv = primary.righe["invoices"][0]
        assert await invoice_service.emetti(primary, FakeOpenapi(), inv) == "da_emettere"


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
        # rete di sicurezza: mai un cessionario nullo trasmesso a SDI
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

    async def test_in_invio_stantie_tornano_a_errore(self):
        primary = FakePrimary({"invoices": [{
            "id": "inv-stuck", "stato": "in_invio", "tentativi": 0,
            "updated_at": "2020-01-01T00:00:00+00:00",  # vecchissima
        }]})
        n = await invoice_service._recupera_in_invio_stantie(primary, minuti=10)
        assert n == 1
        riga = primary.righe["invoices"][0]
        assert riga["stato"] == "errore" and riga["tentativi"] == 1
