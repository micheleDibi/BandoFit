"""Anagrafica di fatturazione (venditore croato, 0029): schema a 2 tipi con
paese mondiale, VIES NON bloccante (fail-open sul salvataggio, fail-closed
sull'aliquota), gate require_billing_account e router."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.api import deps
from app.api.routers import billing
from app.core.errors import OpenapiTimeoutError, register_exception_handlers
from app.schemas.billing import BillingProfileIn
from app.services import billing_service

USER = "00000000-0000-0000-0000-000000000001"

BASE_IT = {
    "tipo_soggetto": "azienda",
    "denominazione": "ACME Srl",
    "partita_iva": "03930330794",
    "indirizzo": "Via Roma 1",
    "comune": "Catanzaro",
    "provincia": "cz",
    "cap": "88100",
}

BASE_DE = {
    "tipo_soggetto": "azienda", "denominazione": "GmbH", "paese": "DE",
    "partita_iva": "123456789", "indirizzo": "Str. 1", "comune": "Berlin",
    "cap": "10115",
}


# ---------------------------------------------------------------- fake primary


class FakeQuery:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self.op = None
        self.payload = None

    def select(self, *_a, **_k):
        self.op = "select"
        return self

    def upsert(self, payload, **kwargs):
        self.op = "upsert"
        self.payload = payload
        self.store.setdefault("upserts", []).append((self.table, payload, kwargs))
        return self

    def eq(self, *_a):
        return self

    def is_(self, *_a):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a):
        return self

    async def execute(self):
        if self.op == "upsert":
            self.store["righe"][self.table] = [dict(self.payload)]
            return SimpleNamespace(data=[self.payload])
        return SimpleNamespace(data=self.store["righe"].get(self.table, []))


class FakePrimary:
    def __init__(self, righe: dict | None = None):
        self.store = {"righe": righe or {}, "upserts": []}

    def table(self, name):
        return FakeQuery(name, self.store)


class FakeOpenapi:
    def __init__(self, enabled=True, esito=True, errore=None):
        self.enabled = enabled
        self.esito = esito
        self.errore = errore  # eccezione da sollevare (VIES giù)
        self.chiamate = []

    async def verifica_piva_ue(self, paese, piva):
        self.chiamate.append((paese, piva))
        if self.errore is not None:
            raise self.errore
        return self.esito


# -------------------------------------------------------------------- schema


class TestSchema:
    def test_azienda_it_valida(self):
        p = BillingProfileIn(**BASE_IT)
        assert p.provincia == "CZ"  # normalizzata maiuscola
        assert p.paese == "IT"

    def test_campi_sdi_rimossi_dal_modello(self):
        # I recapiti SDI erano dell'era «venditore italiano»: fuori dall'API.
        assert "codice_destinatario" not in BillingProfileIn.model_fields
        assert "pec" not in BillingProfileIn.model_fields

    def test_piva_it_non_numerica_rifiutata(self):
        with pytest.raises(ValidationError, match="partita IVA"):
            BillingProfileIn(**{**BASE_IT, "partita_iva": "ABC"})

    def test_piva_ue_normalizzata_senza_prefisso(self):
        # L'utente digita il prefisso paese: si toglie (forma VIES).
        p = BillingProfileIn(**{**BASE_DE, "partita_iva": "DE 123.456.789"})
        assert p.partita_iva == "123456789"

    def test_azienda_extra_ue_valida(self):
        p = BillingProfileIn(
            tipo_soggetto="azienda", denominazione="ACME Inc.", paese="US",
            partita_iva="12-3456789", indirizzo="5th Ave 1", comune="New York",
            cap="10001",
        )
        assert p.paese == "US" and p.provincia is None

    def test_piva_extra_ue_non_viene_corrotta_dal_prefix_strip(self):
        # Lo strip del prefisso vale solo per la UE: uno svizzero 'CHE...' non
        # deve perdere le prime lettere (paese CH non è in PAESI_UE).
        p = BillingProfileIn(
            tipo_soggetto="azienda", denominazione="AG", paese="CH",
            partita_iva="CHE-123.456.789", indirizzo="Bahnhofstr. 1",
            comune="Zürich", cap="8001",
        )
        # Spazi/punti tolti, ma il prefisso 'CHE' resta integro (non è UE).
        assert p.partita_iva == "CHE-123456789"

    def test_cap_e_provincia_obbligatori_solo_per_l_italia(self):
        # IT: CAP 5 cifre + provincia; estero: liberi.
        with pytest.raises(ValidationError, match="CAP|provincia"):
            BillingProfileIn(**{**BASE_IT, "cap": "8810", "provincia": None})
        assert BillingProfileIn(**{**BASE_DE, "cap": "EC1A-1BB"}).cap == "EC1A-1BB"

    def test_privato_it_richiede_cf_nome_cognome(self):
        p = BillingProfileIn(
            tipo_soggetto="privato", nome="Anna", cognome="Bianchi",
            codice_fiscale="bncnna80a41h501z", indirizzo="Via X 2",
            comune="Roma", provincia="RM", cap="00100",
        )
        assert p.codice_fiscale == "BNCNNA80A41H501Z"
        with pytest.raises(ValidationError, match="codice fiscale"):
            BillingProfileIn(
                tipo_soggetto="privato", nome="Anna", cognome="Bianchi",
                indirizzo="Via X 2", comune="Roma", provincia="RM", cap="00100",
            )

    def test_privato_estero_senza_cf_valido(self):
        # Assunzione A1: nessun identificativo fiscale per i privati esteri.
        p = BillingProfileIn(
            tipo_soggetto="privato", nome="Jean", cognome="Dupont", paese="FR",
            indirizzo="Rue X 1", comune="Paris", cap="75001",
        )
        assert p.codice_fiscale is None


# ------------------------------------------------------------------- servizio


class TestServizio:
    async def test_azienda_it_ora_chiama_il_vies(self):
        # Il venditore è croato: l'Italia è un paese UE come gli altri.
        primary = FakePrimary()
        openapi = FakeOpenapi(esito=True)
        out = await billing_service.save_billing_profile(
            primary, openapi, USER, BillingProfileIn(**BASE_IT)
        )
        assert openapi.chiamate == [("IT", "03930330794")]
        assert out.vies_valid is True and out.vies_checked_at
        tabella, payload, kwargs = primary.store["upserts"][0]
        assert tabella == "billing_profiles"
        assert kwargs == {"on_conflict": "user_id"}
        assert payload["user_id"] == USER
        assert "codice_destinatario" not in payload  # colonna congelata

    async def test_azienda_hr_non_chiama_il_vies(self):
        # Vendita domestica: l'esito non cambierebbe l'aliquota (25%).
        openapi = FakeOpenapi()
        dati = BillingProfileIn(
            tipo_soggetto="azienda", denominazione="d.o.o.", paese="HR",
            partita_iva="95855486565", indirizzo="Ulica 1", comune="Umag",
            cap="52470",
        )
        out = await billing_service.save_billing_profile(FakePrimary(), openapi, USER, dati)
        assert openapi.chiamate == []
        assert out.vies_valid is None

    async def test_azienda_extra_ue_non_chiama_il_vies(self):
        openapi = FakeOpenapi()
        dati = BillingProfileIn(
            tipo_soggetto="azienda", denominazione="Inc.", paese="US",
            partita_iva="12-3456789", indirizzo="5th Ave 1", comune="NY",
            cap="10001",
        )
        out = await billing_service.save_billing_profile(FakePrimary(), openapi, USER, dati)
        assert openapi.chiamate == []
        assert out.vies_valid is None

    async def test_privato_non_chiama_il_vies(self):
        openapi = FakeOpenapi()
        dati = BillingProfileIn(
            tipo_soggetto="privato", nome="Anna", cognome="Bianchi",
            codice_fiscale="BNCNNA80A41H501Z", indirizzo="Via X 2",
            comune="Roma", provincia="RM", cap="00100",
        )
        await billing_service.save_billing_profile(FakePrimary(), openapi, USER, dati)
        assert openapi.chiamate == []

    async def test_ue_valida_persiste_la_prova_vies(self):
        primary = FakePrimary()
        openapi = FakeOpenapi(esito=True)
        out = await billing_service.save_billing_profile(
            primary, openapi, USER, BillingProfileIn(**BASE_DE)
        )
        assert openapi.chiamate == [("DE", "123456789")]
        assert out.vies_valid is True and out.vies_checked_at

    async def test_ue_invalida_si_salva_comunque_con_esito_negativo(self):
        # Non più 400: la validità VIES seleziona l'aliquota (25%), non la
        # legittimità del cliente.
        primary = FakePrimary()
        openapi = FakeOpenapi(esito=False)
        out = await billing_service.save_billing_profile(
            primary, openapi, USER, BillingProfileIn(**BASE_DE)
        )
        assert out.vies_valid is False and out.vies_checked_at
        assert len(primary.store["upserts"]) == 1

    async def test_vies_giu_salva_senza_esito(self):
        # Fail-open sul salvataggio, fail-closed sull'aliquota (NULL → 25%).
        primary = FakePrimary()
        openapi = FakeOpenapi(errore=OpenapiTimeoutError())
        out = await billing_service.save_billing_profile(
            primary, openapi, USER, BillingProfileIn(**BASE_DE)
        )
        assert out.vies_valid is None and out.vies_checked_at is None
        assert len(primary.store["upserts"]) == 1  # un solo upsert, riuscito

    async def test_vies_eccezione_generica_non_blocca_il_salvataggio(self):
        # Anche un guasto non previsto (envelope inatteso del provider →
        # AttributeError) deve lasciar salvare: la garanzia è «riesce sempre».
        primary = FakePrimary()
        openapi = FakeOpenapi(errore=AttributeError("boom"))
        out = await billing_service.save_billing_profile(
            primary, openapi, USER, BillingProfileIn(**BASE_DE)
        )
        assert out.vies_valid is None
        assert len(primary.store["upserts"]) == 1

    async def test_openapi_spento_salva_senza_esito(self):
        out = await billing_service.save_billing_profile(
            FakePrimary(), FakeOpenapi(enabled=False),
            USER, BillingProfileIn(**BASE_DE),
        )
        assert out.vies_valid is None

    async def test_map_tollera_i_tipi_legacy(self):
        # Il backend può girare prima della migration 0029: i valori vecchi
        # a DB non devono rompere GET/checkout.
        primary = FakePrimary({
            "billing_profiles": [{
                "tipo_soggetto": "azienda_ue", "denominazione": "GmbH",
                "paese": "DE", "partita_iva": "123456789",
                "indirizzo": "Str. 1", "comune": "Berlin", "cap": "10115",
            }]
        })
        out = await billing_service.get_billing_profile(primary, USER)
        assert out is not None and out.tipo_soggetto == "azienda"

    async def test_prefill_dai_dati_aziendali(self):
        primary = FakePrimary({
            "company_profiles": [{
                "ragione_sociale": "ACME Srl", "partita_iva": "03930330794",
                "codice_fiscale": None, "indirizzo": "Via Roma 1",
                "comune": "Catanzaro", "provincia": "CZ", "cap": "88100",
            }]
        })
        out = await billing_service.get_prefill(primary, USER)
        assert out.tipo_soggetto == "azienda"
        assert out.denominazione == "ACME Srl"

    async def test_profilo_assente(self):
        assert await billing_service.get_billing_profile(FakePrimary(), USER) is None


# ------------------------------------------------------------ gate e endpoint


def _make_client(membership=None, openapi=None) -> httpx.AsyncClient:
    from app.services import family_service

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(billing.router, prefix="/api/v1")
    app.dependency_overrides[deps.get_current_user] = lambda: {"id": USER, "role": "cliente"}
    app.dependency_overrides[deps.get_primary] = lambda: FakePrimary()
    app.dependency_overrides[deps.get_openapi] = lambda: openapi or FakeOpenapi()

    async def fake_membership(_primary, _uid):
        return membership

    # get_membership è chiamata dentro la dependency, non overridabile: monkeypatch.
    family_service.get_membership, fake_membership.originale = (
        fake_membership, family_service.get_membership,
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def _ripristina_membership():
    from app.services import family_service

    originale = family_service.get_membership
    yield
    family_service.get_membership = originale


class TestEndpoint:
    async def test_figlio_attivo_bloccato(self, _ripristina_membership):
        client = _make_client(membership={"status": "active"})
        resp = await client.get("/api/v1/me/billing-profile")
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden"

    async def test_demoted_e_un_account_indipendente(self, _ripristina_membership):
        client = _make_client(membership={"status": "demoted"})
        resp = await client.get("/api/v1/me/billing-profile")
        assert resp.status_code == 200
        assert resp.json() is None  # nessun profilo ancora

    async def test_put_valida_e_ritorna_il_profilo(self, _ripristina_membership):
        client = _make_client(membership=None)
        resp = await client.put("/api/v1/me/billing-profile", json=BASE_IT)
        assert resp.status_code == 200
        assert resp.json()["denominazione"] == "ACME Srl"

    async def test_put_invalido_e_un_422(self, _ripristina_membership):
        client = _make_client(membership=None)
        dati = {**BASE_IT}
        del dati["partita_iva"]
        resp = await client.put("/api/v1/me/billing-profile", json=dati)
        assert resp.status_code == 422
