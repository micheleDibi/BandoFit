"""API del consorzio della call (WP8): flag spento → 404 anche senza token e
con un corpo malformato su ogni rotta nuova; rotte registrate; solo
l'azienda creatrice e le controparti accettate vedono il consorzio; il
membro con visibilità dell'azienda creatrice legge soltanto; un Advisor con
due aziende non vede né tocca dall'altra azienda; forme e codici delle
risposte; nei body mai `company_profile_id`, owner, P.IVA, ragione sociale,
email o codice pubblico dell'altra parte, né valori esatti di bilancio
altrui.

Mini-app con i router veri e `dependency_overrides`; dietro, i servizi veri
sul primario finto del WP8 (`FakePrimaryWP8`) caricato con l'esempio guida."""

import copy

import httpx
import pytest
from fastapi import FastAPI

from app.api import deps
from app.api.deps import ActiveCompany
from app.api.routers import (
    partenariati_candidature,
    partenariati_consorzio,
    partner_calls,
)
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from tests.fixtures.partenariati import esempio_guida as g
from tests.test_partenariato_candidature_service import (  # noqa: F401 — fixture
    MEMBRO_X,
    fixture_fondo,
)
from tests.test_partenariato_consorzio_service import (
    NUMERI_X,
    NUMERI_Y,
    accetta,
    canary,
    consorzio_xy,
    imposta_regole,
    regole,
    scenario_wp8,
)
from tests.test_partenariato_indice import (  # noqa: F401 — fixture autouse
    RAGIONE,
    ambiente_wp6,
)

CALL_FINTA = "e0000000-0000-4000-8000-00000000abcd"
MEMBRO_FINTO = "90000000-0000-4000-8000-00000000abcd"
BASE = f"/api/v1/partenariati/call/{CALL_FINTA}/consorzio"
ROTTE = [
    ("GET", BASE),
    ("PUT", f"{BASE}/membri/{MEMBRO_FINTO}"),
    ("POST", f"{BASE}/membri/{MEMBRO_FINTO}/conferma"),
    ("POST", f"{BASE}/membri/{MEMBRO_FINTO}/esci"),
    ("POST", f"{BASE}/esterni"),
    ("PUT", f"{BASE}/esterni/{MEMBRO_FINTO}"),
    ("PUT", f"{BASE}/budget"),
    ("PUT", f"{BASE}/documenti/nda"),
]
COMPANY_X2 = "c0000000-0000-4000-8000-000000000012"
COMPANY_Y2 = "c0000000-0000-4000-8000-000000000022"


def utente(nome: str) -> dict:
    return {"id": g.OWNER[nome], "role": "cliente", "is_active": True}


def attiva(nome: str, *, editable: bool = True, company: str | None = None) -> ActiveCompany:
    return ActiveCompany(company_id=company or g.COMPANY[nome], owner_id=g.OWNER[nome],
                         editable=editable)


def _http(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def mini_app(db, sec, *, user: dict, active: ActiveCompany) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    for router in (partner_calls.router, partenariati_candidature.router,
                   partenariati_consorzio.router):
        app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[deps.get_current_user] = lambda: user
    app.dependency_overrides[deps.active_company] = lambda: active
    app.dependency_overrides[deps.get_primary] = lambda: db
    app.dependency_overrides[deps.get_secondary] = lambda: sec
    return app


async def chiama(db, sec, nome: str, metodo: str, percorso: str, *, active=None, user=None,
                 **k) -> httpx.Response:
    app = mini_app(db, sec, user=user or utente(nome), active=active or attiva(nome))
    async with _http(app) as client:
        return await client.request(metodo, f"/api/v1{percorso}", **k)


C = f"/partenariati/call/{g.CALL_GUIDA_ID}/consorzio"


# ------------------------------------------------------------ flag


class TestFlag:
    @pytest.fixture
    def spento(self, monkeypatch):
        monkeypatch.setenv("PARTENARIATI_ATTIVO", "false")
        get_settings.cache_clear()

    @pytest.mark.parametrize(("metodo", "percorso"), ROTTE)
    async def test_404_senza_token_anche_con_corpo_malformato(self, spento, metodo, percorso):
        from app.main import app

        async with _http(app) as client:
            resp = await client.request(metodo, percorso, content=b"{malformato")
        assert resp.status_code == 404
        assert resp.json() == {"error": {"code": "not_found", "message": "Risorsa non trovata"}}

    async def test_rotte_registrate(self, monkeypatch):
        monkeypatch.setenv("PARTENARIATI_ATTIVO", "true")
        get_settings.cache_clear()
        from app.main import app

        percorsi = app.openapi()["paths"]
        radice = "/api/v1/partenariati/call/{call_id}/consorzio"
        for percorso in (radice, f"{radice}/membri/{{membro_id}}",
                         f"{radice}/membri/{{membro_id}}/conferma",
                         f"{radice}/membri/{{membro_id}}/esci", f"{radice}/esterni",
                         f"{radice}/esterni/{{membro_id}}", f"{radice}/budget",
                         f"{radice}/documenti/{{codice}}"):
            assert percorso in percorsi, percorso


# ------------------------------------------------------------ flusso e forme


class TestFlusso:
    async def test_creatore_e_membro(self, fondo):
        db, sec = await scenario_wp8()
        x, y = await consorzio_xy(db, sec)
        creatore = await chiama(db, sec, "X", "GET", C)
        assert creatore.status_code == 200, creatore.text
        corpo = creatore.json()
        assert set(corpo) == {"membri", "validazione", "matrice", "documenti", "budget",
                              "forma", "editable", "sei_creatore", "modificabile",
                              "validazione_at", "membri_max"}
        assert corpo["modificabile"] is True
        assert corpo["validazione"]["esito"] == "verde"
        assert (corpo["sei_creatore"], corpo["budget"]["esatto"]) == (True, "3100000.00")
        canary(creatore.text, "Y", numeri=NUMERI_Y)
        membro = await chiama(db, sec, "Y", "GET", C)
        assert membro.status_code == 200
        assert membro.json()["sei_creatore"] is False
        canary(membro.text, "X", numeri=NUMERI_X)
        # 75/25: rosso sulla quota massima, e Y deve riconfermare
        risposta = await chiama(db, sec, "X", "PUT", f"{C}/membri/{x['id']}",
                                json={"ruolo": "capofila", "quota_percentuale": "75"})
        assert risposta.status_code == 200
        risposta = await chiama(db, sec, "X", "PUT", f"{C}/membri/{y['id']}",
                                json={"ruolo": "partner", "posizione_id": g.POS_P1,
                                      "quota_percentuale": "25"})
        assert risposta.json()["validazione"]["esito"] == "rosso"
        quota = next(v for v in risposta.json()["validazione"]["voci"] if v["id"] == "quota:Q1")
        assert "quota massima 70%" in quota["dettaglio_pubblico"]
        # la pagina di Y mostrava ancora il 30%: la conferma non passa (409) e
        # la riga resta da confermare; con i termini nuovi sì
        vecchia = await chiama(db, sec, "Y", "POST", f"{C}/membri/{y['id']}/conferma",
                               json={"ruolo": "partner", "posizione_id": g.POS_P1,
                                     "quota_percentuale": "30"})
        assert vecchia.status_code == 409
        assert vecchia.json()["error"]["code"] == "membro_modificato"
        assert db.membro_di(g.COMPANY["Y"])["stato"] == "proposto"
        senza_corpo = await chiama(db, sec, "Y", "POST", f"{C}/membri/{y['id']}/conferma")
        assert senza_corpo.status_code == 422
        conferma = await chiama(db, sec, "Y", "POST", f"{C}/membri/{y['id']}/conferma",
                                json={"ruolo": "partner", "posizione_id": g.POS_P1,
                                      "quota_percentuale": "25"})
        assert conferma.status_code == 200
        propria = next(m for m in conferma.json()["membri"] if m["sei_tu"])
        assert propria["stato"] == "confermato" and propria["nome"] == RAGIONE["Y"]
        canary(conferma.text, "X", numeri=NUMERI_X)

    async def test_esterni_documenti_budget_e_codici(self, fondo):
        db, sec = await scenario_wp8()
        await consorzio_xy(db, sec)
        esterno = await chiama(db, sec, "X", "POST", f"{C}/esterni", json={
            "denominazione": "Fraunhofer Institut", "paese": "de",
            "tipi_soggetto": ["organismo_ricerca"], "quota_percentuale": "10"})
        assert esterno.status_code == 201, esterno.text
        eid = next(m["id"] for m in esterno.json()["membri"] if m["esterno"])
        modifica = await chiama(db, sec, "X", "PUT", f"{C}/esterni/{eid}", json={
            "denominazione": "Fraunhofer Institut", "paese": "DE",
            "tipi_soggetto": ["organismo_ricerca", "universita"], "quota_percentuale": "10"})
        assert modifica.status_code == 200
        documento = await chiama(db, sec, "X", "PUT", f"{C}/documenti/nda",
                                 json={"stato": "fatto", "note": "Firmato da tutti"})
        assert documento.status_code == 200
        assert next(d for d in documento.json()["documenti"]
                    if d["codice"] == "nda")["stato"] == "fatto"
        budget = await chiama(db, sec, "X", "PUT", f"{C}/budget",
                              json={"budget_fascia": "2m_5m", "budget_progetto_eur": "2800000"})
        assert budget.status_code == 200 and budget.json()["budget"]["esatto"] == "2800000.00"
        casi = [
            ("POST", f"{C}/esterni", {"denominazione": "Scrivete a info@ente.eu", "paese": "DE",
                                      "tipi_soggetto": ["impresa"]}, 400, "testo_non_conforme"),
            ("POST", f"{C}/esterni", {"denominazione": "Ente", "paese": "DE",
                                      "tipi_soggetto": []}, 422, None),
            ("POST", f"{C}/esterni", {"denominazione": "Ente", "paese": "DE",
                                      "tipi_soggetto": ["impresa"],
                                      "company_profile_id": g.COMPANY["Y"]}, 422, None),
            ("PUT", f"{C}/documenti/codice_ignoto", {"stato": "fatto"}, 400,
             "documento_non_valido"),
            ("PUT", f"{C}/documenti/nda", {"stato": "boh"}, 422, None),
            ("PUT", f"{C}/budget", {"budget_fascia": "500k_1m",
                                    "budget_progetto_eur": "2800000"}, 400, "bad_request"),
            ("PUT", f"{C}/membri/{eid}", {"ruolo": "partner", "quota_percentuale": "0"}, 400,
             "bad_request"),
            ("PUT", f"{C}/membri/non-un-id", {"ruolo": "partner"}, 404, "not_found"),
        ]
        for metodo, percorso, corpo, status, code in casi:
            resp = await chiama(db, sec, "X", metodo, percorso, json=corpo)
            assert resp.status_code == status, (percorso, resp.text)
            if code:
                assert resp.json()["error"]["code"] == code, percorso


# ------------------------------------------------------------ isolamento


class TestIsolamento:
    async def _con_advisor(self):
        """Il consorzio X+Y; gli owner di X e Y hanno anche una seconda azienda
        (Advisor), O è estraneo."""
        db, sec = await scenario_wp8()
        x, y = await consorzio_xy(db, sec)
        for nome, seconda in (("X", COMPANY_X2), ("Y", COMPANY_Y2)):
            riga = copy.deepcopy(db.una("company_profiles", id=g.COMPANY[nome]))
            riga.update(id=seconda, ragione_sociale=f"Seconda Sintetica {nome} Srl",
                        partita_iva=f"3000000000{len(nome)}")
            db.tabelle["company_profiles"].append(riga)
        db.rpcs.clear()
        return db, sec, x, y

    async def test_estranei_e_advisor_con_l_altra_azienda(self, fondo):
        db, sec, x, y = await self._con_advisor()
        casi = [
            ("O", attiva("O")),
            ("X", attiva("X", company=COMPANY_X2)),
            ("Y", attiva("Y", company=COMPANY_Y2)),
        ]
        for nome, active in casi:
            assert (await chiama(db, sec, nome, "GET", C, active=active)).status_code == 404
            for metodo, percorso, corpo in (
                ("PUT", f"{C}/membri/{y['id']}", {"ruolo": "partner"}),
                ("POST", f"{C}/membri/{y['id']}/conferma", {"ruolo": "partner"}),
                ("POST", f"{C}/membri/{y['id']}/esci", None),
                ("POST", f"{C}/membri/{x['id']}/esci", None),
                ("POST", f"{C}/esterni", {"denominazione": "Ente", "paese": "DE",
                                          "tipi_soggetto": ["impresa"]}),
                ("PUT", f"{C}/budget", {"budget_fascia": "2m_5m"}),
                ("PUT", f"{C}/documenti/nda", {"stato": "fatto"}),
            ):
                resp = await chiama(db, sec, nome, metodo, percorso, active=active, json=corpo)
                assert resp.status_code == 404, (nome, percorso, resp.text)
        # nessuna scrittura da chi non poteva
        assert db.chiamate("fn_partner_membro_aggiorna") == []
        assert db.chiamate("fn_partner_membro_esci") == []
        assert db.chiamate("fn_partner_membro_esterno") == []

    async def test_membro_della_creatrice_in_sola_lettura(self, fondo):
        db, sec, x, y = await self._con_advisor()
        membro = attiva("X", editable=False)
        letta = await chiama(db, sec, "X", "GET", C, active=membro, user={"id": MEMBRO_X})
        assert letta.status_code == 200
        corpo = letta.json()
        assert corpo["editable"] is False and corpo["budget"]["modificabile"] is False
        assert not any(m["puo_modificare"] or m["puo_confermare"] or m["puo_uscire"]
                       for m in corpo["membri"])
        canary(letta.text, "Y", numeri=NUMERI_Y)
        for metodo, percorso, corpo in (
            ("PUT", f"{C}/membri/{y['id']}", {"ruolo": "partner"}),
            ("POST", f"{C}/membri/{x['id']}/conferma", {"ruolo": "capofila"}),
            ("POST", f"{C}/esterni", {"denominazione": "Ente", "paese": "DE",
                                      "tipi_soggetto": ["impresa"]}),
            ("PUT", f"{C}/documenti/nda", {"stato": "fatto"}),
        ):
            resp = await chiama(db, sec, "X", metodo, percorso, active=membro,
                                user={"id": MEMBRO_X}, json=corpo)
            assert resp.status_code == 403, (percorso, resp.text)

    async def test_controparte_legge_e_agisce_solo_sulla_propria_riga(self, fondo):
        db, sec, x, y = await self._con_advisor()
        t = await accetta(db, sec, "T")
        vista = await chiama(db, sec, "T", "GET", C)
        assert vista.status_code == 200
        canary(vista.text, "X", "Y", numeri=(*NUMERI_X, *NUMERI_Y))
        for metodo, percorso in (("POST", f"{C}/membri/{y['id']}/conferma"),
                                 ("POST", f"{C}/membri/{y['id']}/esci"),
                                 ("PUT", f"{C}/membri/{t['id']}")):
            resp = await chiama(db, sec, "T", metodo, percorso, json={"ruolo": "partner"})
            assert resp.status_code == 404, percorso
        esce = await chiama(db, sec, "T", "POST", f"{C}/membri/{t['id']}/esci")
        assert esce.status_code == 200
        assert [(m["sei_tu"], m["stato"]) for m in esce.json()["membri"]] == [(True, "uscito")]
        assert esce.json()["budget"]["esatto"] is None
        canary(esce.text, "X", "Y", numeri=(*NUMERI_X, *NUMERI_Y))

    async def test_membro_tolto_dal_creatore_non_legge_piu_nulla(self, fondo):
        """X toglie Y: Y non vede più il consorzio (404) né i riservati della
        call (vista pubblica), anche se la sua candidatura resta accettata."""
        db, sec, x, y = await self._con_advisor()
        await accetta(db, sec, "T")
        tolta = await chiama(db, sec, "X", "POST", f"{C}/membri/{y['id']}/esci")
        assert tolta.status_code == 200
        assert (await chiama(db, sec, "Y", "GET", C)).status_code == 404
        call = await chiama(db, sec, "Y", "GET", f"/partenariati/call/{g.CALL_GUIDA_ID}")
        assert call.status_code == 200
        assert "dettagli_riservati" not in call.json()
        assert "3100000" not in call.text
        for metodo, percorso, corpo in (
            ("POST", f"{C}/membri/{y['id']}/esci", None),
            ("POST", f"{C}/membri/{y['id']}/conferma", {"ruolo": "partner"}),
        ):
            resp = await chiama(db, sec, "Y", metodo, percorso, json=corpo)
            assert resp.status_code == 404, (percorso, resp.text)

    async def test_nessun_id_di_terzi_nei_body(self, fondo):
        db, sec, x, y = await self._con_advisor()
        imposta_regole(db, regole(finanziaria=True))
        await accetta(db, sec, "T")
        for nome, altri, numeri in (("X", ("Y", "T"), NUMERI_Y), ("Y", ("X", "T"), NUMERI_X),
                                    ("T", ("X", "Y"), (*NUMERI_X, *NUMERI_Y))):
            resp = await chiama(db, sec, nome, "GET", C)
            assert resp.status_code == 200
            canary(resp.text, *altri, numeri=numeri)
