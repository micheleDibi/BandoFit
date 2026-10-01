"""API delle call di partenariato (WP5): flag spento → 404 su ogni rotta anche
senza token e con un corpo malformato (app vera); il membro in sola lettura
riceve 403 su ogni scrittura; un Advisor con l'azienda A attiva non vede né
modifica la call di B (404); forme JSON delle risposte (vista del creatore
senza campi interni, lista, gap, 202 dei job AI, anteprima, versioni,
segnalazione 201); errori di validazione (422 per tipi e campi sconosciuti,
400 per le regole di dominio).

Mini-app con i router del modulo e `dependency_overrides`; dietro, il servizio
vero sul primario finto di test_partner_call_service."""

from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI

from app.api import deps
from app.api.deps import ActiveCompany
from app.api.routers import partenariati, partner_calls
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.services import partner_call_service as pcs
from tests.test_partner_call_service import (  # noqa: F401  fixture autouse
    ALTRA_COMPANY,
    ALTRO_OWNER,
    CANARY_RISERVATI,
    COMPANY,
    COMPANY_B,
    OWNER,
    SLUG,
    USER_ALTRO,
    USER_MEMBRO,
    USER_OWNER,
    FakeAi,
    FakeDb,
    FakeSecondary,
    _iso,
    catalogo,
    oggi,
    riga_bando_pubblico,
    stub_settings,
)

CALL_FINTA = "e0000000-0000-0000-0000-000000000001"
ROTTE = [
    ("GET", "/api/v1/partenariati/call"),
    ("POST", "/api/v1/partenariati/call"),
    ("GET", f"/api/v1/partenariati/call/{CALL_FINTA}"),
    ("PATCH", f"/api/v1/partenariati/call/{CALL_FINTA}"),
    ("POST", f"/api/v1/partenariati/call/{CALL_FINTA}/regole"),
    ("POST", f"/api/v1/partenariati/call/{CALL_FINTA}/requisiti/genera"),
    ("PUT", f"/api/v1/partenariati/call/{CALL_FINTA}/requisiti"),
    ("POST", f"/api/v1/partenariati/call/{CALL_FINTA}/posizioni/proposta"),
    ("PUT", f"/api/v1/partenariati/call/{CALL_FINTA}/posizioni"),
    ("POST", f"/api/v1/partenariati/call/{CALL_FINTA}/testi/proposta"),
    ("GET", f"/api/v1/partenariati/call/{CALL_FINTA}/anteprima"),
    ("POST", f"/api/v1/partenariati/call/{CALL_FINTA}/pubblica"),
    ("POST", f"/api/v1/partenariati/call/{CALL_FINTA}/chiudi"),
    ("GET", f"/api/v1/partenariati/call/{CALL_FINTA}/versioni"),
    ("POST", f"/api/v1/partenariati/call/{CALL_FINTA}/consulto"),
    ("GET", f"/api/v1/partenariati/call/{CALL_FINTA}/suggeriti"),
    ("GET", f"/api/v1/partenariati/call/{CALL_FINTA}/match"),
    ("POST", f"/api/v1/partenariati/call/{CALL_FINTA}/salva"),
    ("DELETE", f"/api/v1/partenariati/call/{CALL_FINTA}/salva"),
    ("POST", "/api/v1/partenariati/segnalazioni"),
]

CAMPI_VISTA = {
    "id", "company_profile_id", "editable", "stato", "motivo_chiusura", "versione",
    "wizard_passo", "bando", "ruolo_creatore", "forma_aggregazione_prevista", "anonima",
    "titolo", "descrizione_pubblica", "dettagli_riservati", "profilo_partner_ideale",
    "budget_fascia", "budget_progetto_eur", "quota_creatore_pct", "scadenza_call",
    "visibilita", "override_non_ammesso_motivo", "regole_partenariato", "regole_confermate_at",
    "esclusivita", "posizioni", "gap", "ai_posizioni", "ai_testi", "limiti", "puo_pubblicare",
    "motivi_blocco", "pubblicata_at", "chiusa_at", "sospesa_at", "sospeso_motivo",
    "created_at", "updated_at",
}
CAMPI_GAP = {"requisiti", "riepilogo", "ai_check", "partenariato"}
CAMPI_JOB = {"stato", "avviata_at", "errore", "proposta"}
CAMPI_CARD = {
    "id", "stato", "titolo", "bando", "creatore", "ruolo_creatore", "budget_fascia",
    "scadenza_call", "pubblicata_at", "posizioni_n", "requisiti_cercati_n", "mia",
    "wizard_passo", "updated_at",
    # WP6: contatori della bacheca, match e salvataggio (null/0 per le proprie)
    "match", "salvata", "candidature_ricevute", "posti",
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
    monkeypatch.setattr(pcs, "_spawn", catturati.append)
    yield catturati
    for coro in catturati:
        coro.close()


def _http(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def mini_app(db, *, utente=USER_OWNER, active=None, secondary=None, ai=None) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(partner_calls.router, prefix="/api/v1")
    app.include_router(partenariati.router, prefix="/api/v1")
    if utente is not None:
        app.dependency_overrides[deps.get_current_user] = lambda: utente
    attiva = active or ActiveCompany(company_id=COMPANY, owner_id=OWNER, editable=True)
    app.dependency_overrides[deps.active_company] = lambda: attiva
    app.dependency_overrides[deps.get_primary] = lambda: db
    app.dependency_overrides[deps.get_secondary] = lambda: secondary or FakeSecondary()
    app.dependency_overrides[deps.get_ai] = lambda: ai or FakeAi()
    return app


def membro() -> ActiveCompany:
    return ActiveCompany(company_id=COMPANY, owner_id=OWNER, editable=False)


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
            (metodo, "/api/v1" + rotta.path.replace("{call_id}", CALL_FINTA))
            for rotta in partner_calls.router.routes
            for metodo in rotta.methods
        } | {("POST", "/api/v1/partenariati/segnalazioni")}
        assert dichiarate == set(ROTTE)

    async def test_router_registrato_nell_app(self, flag):
        flag(True)
        from app.main import app

        percorsi = app.openapi()["paths"]
        assert set(percorsi["/api/v1/partenariati/call"]) == {"get", "post"}
        assert set(percorsi["/api/v1/partenariati/call/{call_id}"]) == {"get", "patch"}
        assert "/api/v1/partenariati/segnalazioni" in percorsi


# ------------------------------------------------------------ flag acceso


class TestChiAgisce:
    async def test_senza_token_401(self, flag):
        flag(True)
        async with _http(mini_app(FakeDb(), utente=None)) as client:
            resp = await client.get("/api/v1/partenariati/call")
        assert resp.status_code == 401

    @pytest.mark.parametrize(
        ("metodo", "suffisso", "corpo"),
        [
            ("POST", "", {"bando_slug": SLUG, "ruolo_creatore": "capofila"}),
            ("PATCH", "/{id}", {"titolo": "Un titolo abbastanza lungo"}),
            ("POST", "/{id}/regole",
             {"regole": {"modalita": {"valore": "ammesso", "origine_voce": "modificata"}},
              "esclusivita": False}),
            ("POST", "/{id}/requisiti/genera", None),
            ("PUT", "/{id}/requisiti", {"requisiti": []}),
            ("POST", "/{id}/posizioni/proposta", None),
            ("PUT", "/{id}/posizioni", {"posizioni": []}),
            ("POST", "/{id}/testi/proposta", None),
            ("POST", "/{id}/pubblica", None),
            ("POST", "/{id}/chiudi", {"esito": "annullata"}),
        ],
    )
    async def test_membro_in_sola_lettura_403(self, flag, spawned, metodo, suffisso, corpo):
        flag(True)
        db = FakeDb()
        call = db.call_pronta()
        percorso = "/api/v1/partenariati/call" + suffisso.replace("{id}", call["id"])
        async with _http(mini_app(db, utente=USER_MEMBRO, active=membro())) as client:
            resp = await client.request(metodo, percorso, json=corpo)
            lettura = await client.get(f"/api/v1/partenariati/call/{call['id']}")
        assert resp.status_code == 403 and resp.json()["error"]["code"] == "forbidden"
        assert lettura.status_code == 200 and lettura.json()["editable"] is False
        assert spawned == []
        assert [n for n, _ in db.rpcs if n.startswith("fn_partner_call_")
                and n != "fn_partner_call_chiudi_auto"] == []

    @pytest.mark.parametrize(
        ("metodo", "suffisso", "corpo"),
        [
            ("GET", "", None),
            ("PATCH", "", {"descrizione_pubblica": "Altro testo"}),
            ("GET", "/anteprima", None),
            ("GET", "/versioni", None),
            ("POST", "/requisiti/genera", None),
            ("POST", "/pubblica", None),
            ("POST", "/chiudi", {"esito": "annullata"}),
            ("POST", "/testi/proposta", None),
        ],
    )
    async def test_advisor_con_a_attiva_non_tocca_b(self, flag, spawned, metodo, suffisso, corpo):
        flag(True)
        db = FakeDb()
        call_b = db.call_pronta(company_profile_id=COMPANY_B)
        percorso = f"/api/v1/partenariati/call/{call_b['id']}{suffisso}"
        async with _http(mini_app(db)) as client:
            resp = await client.request(metodo, percorso, json=corpo)
        assert resp.status_code == 404
        assert resp.json()["error"]["message"] == "Call di partenariato non trovata"
        assert db.call(call_b["id"])["stato"] == "bozza"
        assert db.call(call_b["id"])["descrizione_pubblica"] != "Altro testo"

    async def test_un_altro_owner_404(self, flag):
        flag(True)
        db = FakeDb()
        call = db.call_pronta()
        altro = ActiveCompany(company_id=ALTRA_COMPANY, owner_id=ALTRO_OWNER, editable=True)
        async with _http(mini_app(db, utente=USER_ALTRO, active=altro)) as client:
            resp = await client.get(f"/api/v1/partenariati/call/{call['id']}")
        assert resp.status_code == 404

    async def test_id_malformato_404(self, flag):
        flag(True)
        async with _http(mini_app(FakeDb())) as client:
            resp = await client.get("/api/v1/partenariati/call/non-un-uuid")
        assert resp.status_code == 404


class TestForme:
    async def test_crea_e_dettaglio(self, flag):
        flag(True)
        db = FakeDb()
        async with _http(mini_app(db)) as client:
            creata = await client.post("/api/v1/partenariati/call",
                                       json={"bando_slug": SLUG, "ruolo_creatore": "capofila"})
            assert creata.status_code == 201
            corpo = creata.json()
            assert set(corpo) == CAMPI_VISTA
            assert set(corpo["gap"]) == CAMPI_GAP
            assert set(corpo["ai_posizioni"]) == CAMPI_JOB and corpo["ai_posizioni"]["stato"] == (
                "nessuno")
            assert corpo["limiti"]["call_attive"] == {"limite": 3, "usate": 0, "residuo": 3}
            dettaglio = await client.get(f"/api/v1/partenariati/call/{corpo['id']}")
        assert dettaglio.status_code == 200
        for interno in ("family_parent_id", "creato_da", OWNER):
            assert interno not in dettaglio.text

    async def test_lista(self, flag):
        flag(True)
        db = FakeDb()
        db.call_pronta()
        async with _http(mini_app(db)) as client:
            resp = await client.get("/api/v1/partenariati/call?vista=mie")
            sbagliata = await client.get("/api/v1/partenariati/call?vista=altre")
        assert resp.status_code == 200
        corpo = resp.json()
        assert set(corpo) == {"items", "total", "page", "page_size", "total_pages"}
        assert set(corpo["items"][0]) == CAMPI_CARD
        assert CANARY_RISERVATI not in resp.text and COMPANY not in resp.text
        assert corpo["items"][0]["match"] is None and corpo["items"][0]["posti"] == 1
        assert sbagliata.status_code == 422  # vista sconosciuta

    async def test_gap_e_202_dei_job(self, flag, spawned):
        flag(True)
        db = FakeDb()
        call = db.call_pronta()
        base = f"/api/v1/partenariati/call/{call['id']}"
        async with _http(mini_app(db)) as client:
            gap = await client.post(f"{base}/requisiti/genera")
            posizioni = await client.post(f"{base}/posizioni/proposta")
            testi = await client.post(f"{base}/testi/proposta")
            doppio = await client.post(f"{base}/testi/proposta")
            dettaglio = await client.get(base)
        assert gap.status_code == 200 and set(gap.json()) == CAMPI_GAP
        assert posizioni.status_code == 202 and set(posizioni.json()) == CAMPI_JOB
        assert testi.status_code == 202 and testi.json()["stato"] == "in_corso"
        assert doppio.status_code == 409 and doppio.json()["error"]["code"] == "ai_in_corso"
        assert dettaglio.json()["ai_testi"]["stato"] == "in_corso"
        assert len(spawned) == 2

    async def test_anteprima_versioni_e_pubblica(self, flag):
        flag(True)
        db = FakeDb()
        call = db.call_pronta()
        base = f"/api/v1/partenariati/call/{call['id']}"
        async with _http(mini_app(db)) as client:
            anteprima = await client.get(f"{base}/anteprima")
            pubblicata = await client.post(f"{base}/pubblica", json={
                "scadenza_call": (oggi() + timedelta(days=10)).isoformat()})
            versioni = await client.get(f"{base}/versioni")
            ripubblica = await client.post(f"{base}/pubblica")
        assert anteprima.status_code == 200 and set(anteprima.json()) == {"call", "rilievi"}
        assert CANARY_RISERVATI not in anteprima.text and COMPANY not in anteprima.text
        assert pubblicata.status_code == 200 and pubblicata.json()["stato"] == "pubblicata"
        assert pubblicata.json()["scadenza_call"] == (oggi() + timedelta(days=10)).isoformat()
        assert versioni.status_code == 200
        [versione] = versioni.json()
        assert set(versione) == {"versione", "created_at", "snapshot"}
        assert "family_parent_id" not in versioni.text and OWNER not in versioni.text
        assert ripubblica.status_code == 409
        assert ripubblica.json()["error"]["code"] == "stato_call_non_valido"

    async def test_chiudi(self, flag):
        flag(True)
        db = FakeDb()
        call = db.call_pronta()
        async with _http(mini_app(db)) as client:
            resp = await client.post(f"/api/v1/partenariati/call/{call['id']}/chiudi",
                                     json={"esito": "annullata"})
        assert resp.status_code == 200 and resp.json()["stato"] == "chiusa_annullata"

    async def test_segnalazione_201(self, flag):
        flag(True)
        db = FakeDb()
        call = db.call_pronta(stato="pubblicata", pubblicata_at=_iso(),
                              scadenza_call=(oggi() + timedelta(days=9)).isoformat())
        altro = ActiveCompany(company_id=ALTRA_COMPANY, owner_id=ALTRO_OWNER, editable=True)
        corpo = {"oggetto_tipo": "call", "oggetto_id": call["id"], "motivo": "spam_pubblicita",
                 "descrizione": "Pubblicità di un servizio a pagamento", "buona_fede": True}
        async with _http(mini_app(db, utente=USER_ALTRO, active=altro)) as client:
            resp = await client.post("/api/v1/partenariati/segnalazioni", json=corpo)
            doppia = await client.post("/api/v1/partenariati/segnalazioni", json=corpo)
            senza_buona_fede = await client.post("/api/v1/partenariati/segnalazioni",
                                                 json={**corpo, "buona_fede": False})
            id_non_valido = await client.post("/api/v1/partenariati/segnalazioni",
                                              json={**corpo, "oggetto_id": "123"})
        assert resp.status_code == 201
        assert set(resp.json()) == {"id", "stato", "created_at"}
        assert resp.json()["stato"] == "ricevuta"
        assert doppia.status_code == 409
        assert doppia.json()["error"]["code"] == "segnalazione_gia_presente"
        assert senza_buona_fede.status_code == 400
        assert id_non_valido.status_code == 400


class TestValidazione:
    async def test_campi_sconosciuti_422(self, flag):
        flag(True)
        db = FakeDb()
        call = db.con_call()
        async with _http(mini_app(db)) as client:
            creata = await client.post("/api/v1/partenariati/call", json={
                "bando_slug": SLUG, "ruolo_creatore": "capofila", "company_profile_id": COMPANY})
            patch = await client.patch(f"/api/v1/partenariati/call/{call['id']}",
                                       json={"stato": "pubblicata"})
            pubblica = await client.post(f"/api/v1/partenariati/call/{call['id']}/pubblica",
                                         json={"forza": True})
        assert creata.status_code == 422 and patch.status_code == 422
        assert pubblica.status_code == 422
        assert db.call(call["id"])["stato"] == "bozza"

    async def test_regole_di_dominio_400(self, flag):
        flag(True)
        db = FakeDb()
        call = db.con_call()
        async with _http(mini_app(db)) as client:
            resp = await client.patch(f"/api/v1/partenariati/call/{call['id']}",
                                      json={"titolo": "corto"})
        assert resp.status_code == 400 and resp.json()["error"]["code"] == "bad_request"

    async def test_nominativo_409_senza_verifica_dell_identita(self, flag):
        flag(True)
        async with _http(mini_app(FakeDb())) as client:
            resp = await client.post("/api/v1/partenariati/call", json={
                "bando_slug": SLUG, "ruolo_creatore": "capofila", "anonima": False})
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "identita_non_verificata_admin"


class TestBandoSospeso:
    """C4/Q18: il bando sospeso non chiude la call; resta il divieto di
    crearla o pubblicarla."""

    @staticmethod
    def _sospeso() -> FakeSecondary:
        return FakeSecondary(pubblici=[riga_bando_pubblico(stato_effettivo="sospeso")])

    async def test_dettaglio_200_la_call_pubblicata_resta_aperta(self, flag):
        flag(True)
        db = FakeDb()
        call = db.call_pronta(stato="pubblicata", pubblicata_at=_iso(),
                              scadenza_call=(oggi() + timedelta(days=10)).isoformat())
        async with _http(mini_app(db, secondary=self._sospeso())) as client:
            resp = await client.get(f"/api/v1/partenariati/call/{call['id']}")
        assert resp.status_code == 200
        corpo = resp.json()
        assert (corpo["stato"], corpo["motivo_chiusura"]) == ("pubblicata", None)
        assert db.chiamate("fn_partner_call_chiudi_auto") == []

    async def test_pubblica_409_e_crea_409(self, flag):
        flag(True)
        db = FakeDb()
        call = db.call_pronta()
        async with _http(mini_app(db, secondary=self._sospeso())) as client:
            pubblica = await client.post(f"/api/v1/partenariati/call/{call['id']}/pubblica")
            crea = await client.post("/api/v1/partenariati/call",
                                     json={"bando_slug": SLUG, "ruolo_creatore": "capofila"})
        assert pubblica.status_code == 409
        assert pubblica.json()["error"]["code"] == "bando_non_disponibile"
        assert crea.status_code == 409
        assert crea.json()["error"]["code"] == "bando_non_disponibile"
        assert db.call(call["id"])["stato"] == "bozza"
