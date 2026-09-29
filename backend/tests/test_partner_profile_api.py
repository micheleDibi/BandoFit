"""API del profilo partner (WP4): flag spento → 404 su ogni rotta anche senza
token e con un corpo malformato (app vera); forme JSON del contratto; il
membro legge con `editable=false` e senza user_id altrui; `X-Active-Company`
di un altro owner → 404 (resolver vero); anteprima «canary» senza dati
identificativi; informativa.

Mini-app con i router del modulo e `dependency_overrides`; dietro, il servizio
vero sul primario finto di test_partner_profile_service."""

import httpx
import pytest
from fastapi import FastAPI

from app.api import deps
from app.api.deps import ActiveCompany
from app.api.routers import partenariati, partner_profile
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.services import partner_profile_service as pps
from app.services.partenariato_informativa import (
    INFORMATIVA_PARTNER_VERSIONE,
    INFORMATIVA_REFERENTE_VERSIONE,
)
from tests.test_partner_profile_service import (  # noqa: F401  fixture autouse
    ALTRO_MEMBRO,
    COMPANY,
    EMAIL,
    MEMBRO,
    OWNER,
    PEC,
    PIVA,
    USER_MEMBRO,
    USER_OWNER,
    FakeAi,
    FakeDb,
    catalogo,
    stub_settings,
)

ALTRO_OWNER = "a0000000-0000-0000-0000-0000000000ff"
AZIENDA_ALTRUI = "c0000000-0000-0000-0000-0000000000ff"

ROTTE = [
    ("GET", "/api/v1/me/partner-profile"),
    ("PUT", "/api/v1/me/partner-profile"),
    ("POST", "/api/v1/me/partner-profile/consenso"),
    ("POST", "/api/v1/me/partner-profile/referente"),
    ("POST", "/api/v1/me/partner-profile/referente/risposta"),
    ("POST", "/api/v1/me/partner-profile/bozza-ai"),
    ("DELETE", "/api/v1/me/partner-profile/bozza-ai"),
    ("GET", "/api/v1/me/partner-profile/anteprima"),
    ("GET", "/api/v1/me/partner-profile/identita"),
    ("POST", "/api/v1/me/partner-profile/identita"),
    ("GET", "/api/v1/partenariati/informativa"),
]

CAMPI_PROFILO_OUT = {
    "editable", "esiste", "visibile", "anonimo", "sospeso", "consenso",
    "informativa_versione_corrente", "riconsenso_suggerito", "identita", "profilo",
    "tipi_soggetto_dedotti", "completezza", "avvisi_anonimato", "referente",
    "referenti_possibili", "bozza_ai", "vocabolario_versione", "aggiornato_at",
}
CAMPI_VERIFICA = {
    "stato", "verificata", "richiesta_at", "verificata_at", "puo_richiedere",
    "motivo_non_richiedibile",
}
CAMPI_PROFILO_DATI = {
    "descrizione_competenze", "competenze", "competenze_libere", "tipi_soggetto",
    "ruoli_disponibili", "settori_interesse", "regioni_interesse", "paesi_interesse",
    "forme_accettate", "esperienze", "certificazioni", "infrastrutture", "accetta_inviti",
    "categorie_bando_escluse",
}
CAMPI_PUBBLICO = {
    "codice_pubblico", "anonimo", "denominazione", "regione_sede", "regioni_interesse",
    "paesi_interesse", "ateco_sezione", "classe_dimensionale", "fasce", "tipi_soggetto",
    "competenze", "competenze_libere", "descrizione_competenze", "esperienze",
    "certificazioni", "infrastrutture", "ruoli_disponibili", "forme_accettate", "completezza",
    "accetta_inviti",
}


@pytest.fixture
def flag(monkeypatch):
    def imposta(valore: bool) -> None:
        monkeypatch.setenv("PARTENARIATI_ATTIVO", "true" if valore else "false")
        get_settings.cache_clear()

    yield imposta
    get_settings.cache_clear()


@pytest.fixture
def spawned(monkeypatch):
    catturati: list = []
    monkeypatch.setattr(pps, "_spawn", catturati.append)
    yield catturati
    for coro in catturati:
        coro.close()


def _http(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def mini_app(db, *, utente=USER_OWNER, editable=True, ai=None, resolver_vero=False) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(partner_profile.router, prefix="/api/v1")
    app.include_router(partenariati.router, prefix="/api/v1")
    if utente is not None:
        app.dependency_overrides[deps.get_current_user] = lambda: utente
    if not resolver_vero:
        owner = OWNER
        app.dependency_overrides[deps.active_company] = lambda: ActiveCompany(
            company_id=COMPANY, owner_id=owner, editable=editable
        )
    app.dependency_overrides[deps.get_primary] = lambda: db
    app.dependency_overrides[deps.get_secondary] = lambda: object()
    app.dependency_overrides[deps.get_ai] = lambda: ai or FakeAi()
    return app


# ------------------------------------------------------------ flag spento


class TestFlagSpento:
    @pytest.mark.parametrize(("metodo", "percorso"), ROTTE)
    async def test_404_senza_token_sull_app_vera(self, flag, metodo, percorso):
        flag(False)
        from app.main import app

        async with _http(app) as client:
            resp = await client.request(metodo, percorso, json={})
        assert resp.status_code == 404
        assert resp.json() == {"error": {"code": "not_found", "message": "Risorsa non trovata"}}

    @pytest.mark.parametrize(("metodo", "percorso"), ROTTE)
    async def test_404_anche_con_corpo_malformato(self, flag, metodo, percorso):
        flag(False)
        from app.main import app

        async with _http(app) as client:
            resp = await client.request(
                metodo, percorso, content=b"{", headers={"Content-Type": "application/json",
                                                          "Authorization": "Bearer x"},
            )
        assert resp.status_code == 404 and resp.json()["error"]["code"] == "not_found"

    async def test_ogni_rotta_e_coperta(self):
        dichiarate = {
            (metodo, "/api/v1" + rotta.path)
            for rotta in partner_profile.router.routes
            for metodo in rotta.methods
        } | {("GET", "/api/v1/partenariati/informativa")}
        assert dichiarate == set(ROTTE)

    async def test_router_registrato_nell_app(self, flag):
        flag(True)
        from app.main import app

        percorsi = set(app.openapi()["paths"])
        assert "/api/v1/me/partner-profile" in percorsi
        assert "/api/v1/me/partner-profile/anteprima" in percorsi
        assert "/api/v1/partenariati/informativa" in percorsi


# ------------------------------------------------------------ flag acceso


class TestForme:
    async def test_senza_token_401(self, flag):
        flag(True)
        async with _http(mini_app(FakeDb(), utente=None)) as client:
            resp = await client.get("/api/v1/me/partner-profile")
        assert resp.status_code == 401

    async def test_get(self, flag):
        flag(True)
        async with _http(mini_app(FakeDb().con_profilo())) as client:
            resp = await client.get("/api/v1/me/partner-profile")
        assert resp.status_code == 200
        corpo = resp.json()
        assert set(corpo) == CAMPI_PROFILO_OUT
        assert set(corpo["profilo"]) == CAMPI_PROFILO_DATI
        assert set(corpo["identita"]) == {
            "verificata", "motivo", "denominazione_registro", "puo_essere_nominativo",
            "motivo_nominativo", "verifica",
        }
        assert set(corpo["identita"]["verifica"]) == CAMPI_VERIFICA
        assert set(corpo["referente"]) == {"tipo", "nome", "sei_tu", "proposto"}
        assert {tuple(sorted(r)) for r in corpo["referenti_possibili"]} == {("nome", "user_id")}
        assert corpo["editable"] is True and corpo["esiste"] is True
        assert corpo["informativa_versione_corrente"] == INFORMATIVA_PARTNER_VERSIONE
        # nessun campo interno esce
        for interno in ("codice_pubblico", "family_parent_id", "company_profile_id",
                        "bozza_ai_esecuzione_id", PIVA, EMAIL, PEC):
            assert interno not in resp.text

    async def test_put_e_validazione(self, flag):
        flag(True)
        db = FakeDb()
        async with _http(mini_app(db)) as client:
            ok = await client.put("/api/v1/me/partner-profile", json={
                "descrizione_competenze": "Tornitura e fresatura",
                "competenze": ["meccanica_meccatronica"], "paesi_interesse": ["de"],
            })
            protetto = await client.put("/api/v1/me/partner-profile",
                                        json={"anonimo": False})
            ignoto = await client.put("/api/v1/me/partner-profile",
                                      json={"competenze": ["codice_inventato"]})
            contatto = await client.put("/api/v1/me/partner-profile",
                                        json={"descrizione_competenze": f"Scrivi a {EMAIL}"})
        assert ok.status_code == 200
        assert ok.json()["profilo"]["paesi_interesse"] == ["DE"]
        assert protetto.status_code == 422  # extra='forbid': il campo non esiste nel PUT
        assert (ignoto.status_code, ignoto.json()["error"]["code"]) == (400, "bad_request")
        assert contatto.status_code == 400
        assert contatto.json()["error"]["code"] == "testo_non_conforme"
        assert EMAIL not in contatto.text
        assert len(db.upserts) == 1

    async def test_consenso(self, flag):
        flag(True)
        db = FakeDb()
        async with _http(mini_app(db)) as client:
            resp = await client.post("/api/v1/me/partner-profile/consenso", json={
                "azione": "concedi", "informativa_versione": INFORMATIVA_PARTNER_VERSIONE,
                "origine": "import_piva", "anonimo": True,
            })
            superata = await client.post("/api/v1/me/partner-profile/consenso", json={
                "azione": "concedi", "informativa_versione": "vecchia",
                "origine": "pagina_azienda", "anonimo": True,
            })
            admin = await client.post("/api/v1/me/partner-profile/consenso", json={
                "azione": "revoca", "informativa_versione": INFORMATIVA_PARTNER_VERSIONE,
                "origine": "admin",
            })
        assert resp.status_code == 200
        assert resp.json()["visibile"] is True and resp.json()["consenso"]["versione"] == (
            INFORMATIVA_PARTNER_VERSIONE)
        assert (superata.status_code, superata.json()["error"]["code"]) == (
            409, "informativa_superata")
        assert admin.status_code == 422  # il client non può dichiararsi admin
        assert db.consensi[0]["origine"] == "import_piva"

    async def test_referente_e_risposta(self, flag):
        flag(True)
        db = FakeDb().con_profilo()
        async with _http(mini_app(db)) as client:
            proposta = await client.post("/api/v1/me/partner-profile/referente",
                                         json={"azione": "proponi", "user_id": MEMBRO})
        async with _http(mini_app(db, utente=USER_MEMBRO, editable=False)) as client:
            risposta = await client.post("/api/v1/me/partner-profile/referente/risposta", json={
                "azione": "accetta", "informativa_versione": INFORMATIVA_REFERENTE_VERSIONE,
            })
            vietata = await client.post("/api/v1/me/partner-profile/referente",
                                        json={"azione": "rimuovi"})
        assert proposta.status_code == 200
        assert proposta.json()["referente"]["proposto"] == {"nome": "Luca Verdi", "sei_tu": False}
        assert risposta.status_code == 200
        assert risposta.json()["referente"] == {"tipo": "membro", "nome": "Luca Verdi",
                                                "sei_tu": True, "proposto": None}
        assert vietata.status_code == 403 and vietata.json()["error"]["code"] == "forbidden"

    async def test_bozza_ai_202_e_scarto(self, flag, spawned):
        flag(True)
        db = FakeDb()
        async with _http(mini_app(db)) as client:
            avvio = await client.post("/api/v1/me/partner-profile/bozza-ai")
            in_corso = await client.delete("/api/v1/me/partner-profile/bozza-ai")
            await spawned.pop()
            scarto = await client.delete("/api/v1/me/partner-profile/bozza-ai")
        assert avvio.status_code == 202
        assert avvio.json()["bozza_ai"]["stato"] == "in_corso"
        assert set(avvio.json()["bozza_ai"]) == {"stato", "avviata_at", "pronta_at", "errore",
                                                 "proposta"}
        assert (in_corso.status_code, in_corso.json()["error"]["code"]) == (409, "bozza_in_corso")
        assert scarto.status_code == 200 and scarto.json()["bozza_ai"] is None

    async def test_bozza_ai_non_configurata_503(self, flag, spawned):
        flag(True)
        async with _http(mini_app(FakeDb(), ai=FakeAi(enabled=False))) as client:
            resp = await client.post("/api/v1/me/partner-profile/bozza-ai")
        assert (resp.status_code, resp.json()["error"]["code"]) == (503, "ai_not_configured")

    async def test_informativa(self, flag):
        flag(True)
        async with _http(mini_app(FakeDb())) as client:
            resp = await client.get("/api/v1/partenariati/informativa")
        assert resp.status_code == 200
        corpo = resp.json()
        assert set(corpo) == {"versione", "testo", "referente_versione", "referente_testo"}
        assert corpo["versione"] == INFORMATIVA_PARTNER_VERSIONE
        assert corpo["referente_versione"] == INFORMATIVA_REFERENTE_VERSIONE
        assert corpo["testo"].startswith("[BOZZA — DA RIVEDERE CON IL LEGALE]")


class TestMembro:
    async def test_editable_false_e_nessun_user_id_altrui(self, flag):
        flag(True)
        db = FakeDb().con_profilo(referente_user_id=ALTRO_MEMBRO)
        async with _http(mini_app(db, utente=USER_MEMBRO, editable=False)) as client:
            resp = await client.get("/api/v1/me/partner-profile")
            scrittura = await client.put("/api/v1/me/partner-profile", json={})
        corpo = resp.json()
        assert resp.status_code == 200
        assert corpo["editable"] is False and corpo["referenti_possibili"] == []
        assert corpo["referente"]["nome"] == "Anna Neri"
        for uid in (OWNER, ALTRO_MEMBRO, MEMBRO):
            assert uid not in resp.text
        assert (scrittura.status_code, scrittura.json()["error"]["code"]) == (403, "forbidden")


class TestAziendaAttiva:
    """Resolver VERO dell'azienda attiva sul primario finto."""

    def _db(self) -> FakeDb:
        db = FakeDb().con_profilo()
        db.tabelle["company_profiles"].append({
            "id": AZIENDA_ALTRUI, "parent_id": ALTRO_OWNER, "ragione_sociale": "Altra S.p.A.",
            "partita_iva": "11111111111", "deleted_at": None, "archived_at": None,
        })
        db._fn_effective_max_aziende = lambda p: 5
        return db

    @pytest.mark.parametrize("header", [AZIENDA_ALTRUI, "non-un-uuid"])
    @pytest.mark.parametrize("metodo", ["GET", "PUT"])
    async def test_header_di_un_altro_owner_404(self, flag, header, metodo):
        flag(True)
        db = self._db()
        app = mini_app(db, resolver_vero=True)
        async with _http(app) as client:
            resp = await client.request(metodo, "/api/v1/me/partner-profile",
                                        headers={"X-Active-Company": header}, json={})
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"
        assert not [op for op in db.ops if op[0] == "company_partner_profiles"]
        assert db.upserts == [] and "Altra" not in resp.text

    async def test_header_della_propria_azienda(self, flag):
        flag(True)
        app = mini_app(self._db(), resolver_vero=True)
        async with _http(app) as client:
            resp = await client.get("/api/v1/me/partner-profile",
                                    headers={"X-Active-Company": COMPANY})
        assert resp.status_code == 200 and resp.json()["editable"] is True


class TestAnteprima:
    @pytest.mark.parametrize("anonimo", [True, False])
    async def test_canary(self, flag, anonimo):
        flag(True)
        # Il nominativo si vede solo con l'identità verificata dalla
        # piattaforma (WP9): qui c'è.
        db = FakeDb().verifica_identita().con_profilo(
            anonimo=anonimo, referente_user_id=MEMBRO,
            descrizione_competenze=f"Lavorazioni di precisione. Contatti: {EMAIL}",
            competenze=["meccanica_meccatronica"],
            esperienze=[{"programma": "Horizon Europe", "programma_id": 7, "anno": 2023,
                         "ruolo": "capofila", "titolo": "Progetto Alfa"}],
            certificazioni=["ISO 9001:2015"], infrastrutture="Banco prova a tre assi",
        )
        async with _http(mini_app(db, utente=USER_MEMBRO, editable=False)) as client:
            resp = await client.get("/api/v1/me/partner-profile/anteprima")
        assert resp.status_code == 200
        corpo = resp.json()
        assert set(corpo) == CAMPI_PUBBLICO
        assert set(corpo["fasce"]) == {"fatturato", "patrimonio_netto", "dipendenti", "trend"}
        testo = resp.text
        for vietato in (PIVA, COMPANY, OWNER, MEMBRO, EMAIL, PEC, "7654321", "1234567",
                        "Luca Verdi", "family_parent_id", "company_profile_id", "referente",
                        "Mario", "Bianchi"):
            assert vietato not in testo, vietato
        if anonimo:
            assert corpo["denominazione"] is None and corpo["infrastrutture"] is None
            assert "ROSSI" not in testo and "Rossi" not in testo
            assert corpo["esperienze"] == [{"programma": "Horizon Europe", "anno": None,
                                            "ruolo": None, "titolo": None}]
            assert corpo["fasce"]["dipendenti"] is None
            assert corpo["certificazioni"] == ["Gestione della qualità"]
        else:
            assert corpo["denominazione"] == "ROSSI MECCANICA SRL"
            assert corpo["esperienze"][0]["anno"] == 2023
            assert corpo["fasce"]["dipendenti"] is not None


# ------------------------------------------------ verifica dell'identità (WP9)


class TestVerificaIdentita:
    async def test_stato_e_richiesta(self, flag):
        flag(True)
        db = FakeDb()
        async with _http(mini_app(db)) as client:
            stato = await client.get("/api/v1/me/partner-profile/identita")
            richiesta = await client.post("/api/v1/me/partner-profile/identita",
                                          json={"nota": "Chiamate la sede"})
            doppia = await client.post("/api/v1/me/partner-profile/identita")
        assert stato.status_code == 200 and set(stato.json()) == CAMPI_VERIFICA
        assert stato.json()["stato"] == "non_richiesta" and stato.json()["puo_richiedere"]
        assert richiesta.status_code == 200 and richiesta.json()["stato"] == "richiesta"
        assert db.chiamate("fn_identita_richiedi")[0]["p_nota"] == "Chiamate la sede"
        assert (doppia.status_code, doppia.json()["error"]["code"]) == (
            409, "identita_richiesta_aperta")

    async def test_validazione_della_nota(self, flag):
        flag(True)
        db = FakeDb()
        async with _http(mini_app(db)) as client:
            lunga = await client.post("/api/v1/me/partner-profile/identita",
                                      json={"nota": "x" * 501})
            ignoto = await client.post("/api/v1/me/partner-profile/identita",
                                       json={"metodo": "pec"})
        assert lunga.status_code == 422 and ignoto.status_code == 422
        assert db.chiamate("fn_identita_richiedi") == []

    async def test_il_membro_legge_e_non_chiede(self, flag):
        flag(True)
        db = FakeDb().verifica_identita()
        async with _http(mini_app(db, utente=USER_MEMBRO, editable=False)) as client:
            stato = await client.get("/api/v1/me/partner-profile/identita")
            richiesta = await client.post("/api/v1/me/partner-profile/identita")
        assert stato.status_code == 200
        assert stato.json()["verificata"] is True
        assert stato.json()["motivo_non_richiedibile"] == "solo_titolare"
        assert (richiesta.status_code, richiesta.json()["error"]["code"]) == (403, "forbidden")
        # mai chi ha verificato né il metodo
        assert "verificata_da" not in stato.text and "metodo" not in stato.text

    async def test_nominativo_senza_verifica_409(self, flag):
        flag(True)
        db = FakeDb()
        async with _http(mini_app(db)) as client:
            resp = await client.post("/api/v1/me/partner-profile/consenso", json={
                "azione": "concedi", "informativa_versione": INFORMATIVA_PARTNER_VERSIONE,
                "origine": "pagina_azienda", "anonimo": False,
            })
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "identita_non_verificata_admin"
