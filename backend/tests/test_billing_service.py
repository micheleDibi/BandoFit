"""Anagrafica di fatturazione: schema per tipo di soggetto, servizio (VIES
bloccante per l'UE), gate require_billing_account e router."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.api import deps
from app.api.routers import billing
from app.core.errors import (
    BadRequestError,
    OpenapiNotConfiguredError,
    register_exception_handlers,
)
from app.schemas.billing import BillingProfileIn
from app.services import billing_service

USER = "00000000-0000-0000-0000-000000000001"

BASE_IT = {
    "tipo_soggetto": "azienda_it",
    "denominazione": "ACME Srl",
    "partita_iva": "03930330794",
    "indirizzo": "Via Roma 1",
    "comune": "Catanzaro",
    "provincia": "cz",
    "cap": "88100",
    "codice_destinatario": "abc1234",
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
    def __init__(self, enabled=True, esito=True):
        self.enabled = enabled
        self.esito = esito
        self.chiamate = []

    async def verifica_piva_ue(self, paese, piva):
        self.chiamate.append((paese, piva))
        return self.esito


# -------------------------------------------------------------------- schema


class TestSchema:
    def test_azienda_it_valida(self):
        p = BillingProfileIn(**BASE_IT)
        assert p.provincia == "CZ"  # normalizzata maiuscola
        assert p.codice_destinatario == "ABC1234"

    def test_azienda_it_senza_recapito_sdi_rifiutata(self):
        dati = {**BASE_IT}
        del dati["codice_destinatario"]
        with pytest.raises(ValidationError, match="codice destinatario|PEC"):
            BillingProfileIn(**dati)

    def test_azienda_it_con_pec_al_posto_del_codice(self):
        dati = {**BASE_IT, "codice_destinatario": None, "pec": "acme@pec.it"}
        assert BillingProfileIn(**dati).pec == "acme@pec.it"

    def test_piva_non_numerica_rifiutata(self):
        with pytest.raises(ValidationError, match="partita IVA"):
            BillingProfileIn(**{**BASE_IT, "partita_iva": "ABC"})

    def test_privato_richiede_cf_nome_cognome(self):
        p = BillingProfileIn(
            tipo_soggetto="privato_it", nome="Anna", cognome="Bianchi",
            codice_fiscale="bncnna80a41h501z", indirizzo="Via X 2",
            comune="Roma", provincia="RM", cap="00100",
            codice_destinatario="ABC1234",  # ignorato: B2C viaggia con 0000000
        )
        assert p.codice_fiscale == "BNCNNA80A41H501Z"
        assert p.codice_destinatario is None
        with pytest.raises(ValidationError, match="codice fiscale"):
            BillingProfileIn(
                tipo_soggetto="privato_it", nome="Anna", cognome="Bianchi",
                indirizzo="Via X 2", comune="Roma", cap="00100",
            )

    def test_azienda_ue_paese_it_rifiutato(self):
        with pytest.raises(ValidationError, match="paese"):
            BillingProfileIn(
                tipo_soggetto="azienda_ue", denominazione="GmbH", paese="IT",
                partita_iva="DE123456789", indirizzo="Str. 1", comune="Berlin",
                cap="10115",
            )

    def test_azienda_ue_valida_senza_codice_destinatario(self):
        p = BillingProfileIn(
            tipo_soggetto="azienda_ue", denominazione="GmbH", paese="DE",
            partita_iva="123456789", indirizzo="Str. 1", comune="Berlin",
            cap="10115",
        )
        assert p.codice_destinatario is None  # deciso dal builder XML (XXXXXXX)


# ------------------------------------------------------------------- servizio


class TestServizio:
    async def test_salvataggio_azienda_it_non_chiama_il_vies(self):
        primary = FakePrimary()
        openapi = FakeOpenapi()
        out = await billing_service.save_billing_profile(
            primary, openapi, USER, BillingProfileIn(**BASE_IT)
        )
        assert openapi.chiamate == []
        assert out.vies_valid is None
        tabella, payload, kwargs = primary.store["upserts"][0]
        assert tabella == "billing_profiles"
        assert kwargs == {"on_conflict": "user_id"}
        assert payload["user_id"] == USER

    async def test_ue_valida_persiste_la_prova_vies(self):
        primary = FakePrimary()
        openapi = FakeOpenapi(esito=True)
        dati = BillingProfileIn(
            tipo_soggetto="azienda_ue", denominazione="GmbH", paese="DE",
            partita_iva="123456789", indirizzo="Str. 1", comune="Berlin", cap="10115",
        )
        out = await billing_service.save_billing_profile(primary, openapi, USER, dati)
        assert openapi.chiamate == [("DE", "123456789")]
        assert out.vies_valid is True and out.vies_checked_at

    async def test_ue_invalida_blocca_senza_scrivere(self):
        primary = FakePrimary()
        openapi = FakeOpenapi(esito=False)
        dati = BillingProfileIn(
            tipo_soggetto="azienda_ue", denominazione="GmbH", paese="DE",
            partita_iva="999999999", indirizzo="Str. 1", comune="Berlin", cap="10115",
        )
        with pytest.raises(BadRequestError, match="VIES"):
            await billing_service.save_billing_profile(primary, openapi, USER, dati)
        assert primary.store["upserts"] == []

    async def test_ue_senza_openapi_configurato(self):
        dati = BillingProfileIn(
            tipo_soggetto="azienda_ue", denominazione="GmbH", paese="DE",
            partita_iva="123456789", indirizzo="Str. 1", comune="Berlin", cap="10115",
        )
        with pytest.raises(OpenapiNotConfiguredError):
            await billing_service.save_billing_profile(
                FakePrimary(), FakeOpenapi(enabled=False), USER, dati
            )

    async def test_prefill_dai_dati_aziendali(self):
        primary = FakePrimary({
            "company_profiles": [{
                "ragione_sociale": "ACME Srl", "partita_iva": "03930330794",
                "codice_fiscale": None, "indirizzo": "Via Roma 1",
                "comune": "Catanzaro", "provincia": "CZ", "cap": "88100",
                "pec": "acme@pec.it",
            }]
        })
        out = await billing_service.get_prefill(primary, USER)
        assert out.tipo_soggetto == "azienda_it"
        assert out.denominazione == "ACME Srl" and out.pec == "acme@pec.it"

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
        del dati["codice_destinatario"]
        resp = await client.put("/api/v1/me/billing-profile", json=dati)
        assert resp.status_code == 422
