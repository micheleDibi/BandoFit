"""API della moderazione, dell'admin e della verifica dell'identità (WP9):
flag spento → 404 anche senza token e con un corpo malformato su ogni rotta
nuova; con il flag acceso le rotte admin rispondono solo a un AdminUser (403
a cliente e progettista, 401 senza token); forme e codici delle risposte;
nei body verso autore e segnalante mai l'identità dell'altra parte, verso
l'admin mai id di utenti.

Mini-app con i router veri e `dependency_overrides`; dietro, i servizi veri
sul primario finto del WP9 caricato con l'esempio guida. (La copertura di
tutte le rotte dei router del modulo è in test_partenariati_api.)"""

import httpx
import pytest
from fastapi import FastAPI

from app.api import deps
from app.api.deps import ActiveCompany
from app.api.routers import admin_partenariati, partenariati
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.services import partenariato_moderazione_testi as testi
from tests.fixtures.partenariati import esempio_guida as g
from tests.test_partenariato_candidature_service import fixture_fondo  # noqa: F401
from tests.test_partenariato_indice import (  # noqa: F401 — fixture autouse
    RAGIONE,
    ambiente_wp6,
)
from tests.test_partenariato_moderazione_service import (  # noqa: F401 — fixture
    ADMIN,
    ADMIN_ID,
    DESCRIZIONE,
    MOTIVAZIONE,
    MOTIVAZIONE_RICORSO,
    TESTO_RICORSO,
    canary,
    fixture_posta,
    scenario_wp9,
    segnala_call,
)

SEG = "5e000000-0000-4000-8000-000000000001"
CALL = "e0000000-0000-4000-8000-00000000abcd"
AZIENDA = "c0000000-0000-4000-8000-00000000abcd"
A = "/api/v1/admin/partenariati"
ROTTE = [
    ("GET", f"/api/v1/partenariati/segnalazioni/{SEG}"),
    ("POST", f"/api/v1/partenariati/segnalazioni/{SEG}/ricorso"),
    ("GET", f"{A}/segnalazioni"),
    ("GET", f"{A}/segnalazioni/{SEG}"),
    ("POST", f"{A}/segnalazioni/{SEG}/prendi"),
    ("POST", f"{A}/segnalazioni/{SEG}/anteprima"),
    ("POST", f"{A}/segnalazioni/{SEG}/decidi"),
    ("POST", f"{A}/segnalazioni/{SEG}/ricorso/decidi"),
    ("GET", f"{A}/segnalazioni/{SEG}/contesto"),
    ("POST", f"{A}/segnalazioni/{SEG}/contesto"),
    ("GET", f"{A}/call"),
    ("POST", f"{A}/call/{CALL}/sospendi"),
    ("POST", f"{A}/profilo/{CALL}/ripristina"),
    ("GET", f"{A}/metriche"),
    ("GET", f"{A}/costi"),
    ("GET", f"{A}/identita"),
    ("POST", f"{A}/identita/{AZIENDA}/decidi"),
    ("POST", f"{A}/identita/{AZIENDA}/revoca"),
]
ADMIN_ROTTE = [r for r in ROTTE if r[1].startswith(A)]


def utente(nome: str) -> dict:
    return {"id": g.OWNER[nome], "role": "cliente", "is_active": True}


def attiva(nome: str, *, editable: bool = True) -> ActiveCompany:
    return ActiveCompany(company_id=g.COMPANY[nome], owner_id=g.OWNER[nome], editable=editable)


def _http(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def mini_app(db, *, user: dict | None, active: ActiveCompany | None = None) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    for router in (partenariati.router, admin_partenariati.router):
        app.include_router(router, prefix="/api/v1")
    if user is not None:
        app.dependency_overrides[deps.get_current_user] = lambda: user
    app.dependency_overrides[deps.active_company] = lambda: active or attiva("O")
    app.dependency_overrides[deps.get_primary] = lambda: db
    app.dependency_overrides[deps.get_secondary] = lambda: None
    return app


async def chiama(db, metodo: str, percorso: str, *, user=ADMIN, active=None,
                 **k) -> httpx.Response:
    async with _http(mini_app(db, user=user, active=active)) as client:
        return await client.request(metodo, percorso, **k)


# ------------------------------------------------------------ flag e ruoli


class TestFlagERuoli:
    @pytest.fixture
    def spento(self, monkeypatch):
        monkeypatch.setenv("PARTENARIATI_ATTIVO", "false")
        get_settings.cache_clear()

    @pytest.mark.parametrize(("metodo", "percorso"), ROTTE)
    async def test_404_senza_token_anche_con_corpo_malformato(self, spento, metodo, percorso):
        from app.main import app

        async with _http(app) as client:
            resp = await client.request(metodo, percorso, content=b"{malformato",
                                        headers={"Content-Type": "application/json"})
        assert resp.status_code == 404
        assert resp.json() == {"error": {"code": "not_found", "message": "Risorsa non trovata"}}

    @pytest.mark.parametrize(("metodo", "percorso"), ADMIN_ROTTE)
    @pytest.mark.parametrize("ruolo", ["cliente", "progettista"])
    async def test_admin_solo_admin(self, metodo, percorso, ruolo):
        db, _ = await scenario_wp9()
        resp = await chiama(db, metodo, percorso, user={"id": g.OWNER["O"], "role": ruolo},
                            json={"motivazione": MOTIVAZIONE, "decisione": "nessuna_azione"})
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden"
        assert db.rpcs == [] and not [o for o in db.ops if o["op"] != "select"]

    @pytest.mark.parametrize(("metodo", "percorso"), ROTTE)
    async def test_senza_token_401_a_flag_acceso(self, metodo, percorso):
        db, _ = await scenario_wp9()
        resp = await chiama(db, metodo, percorso, user=None, json={})
        assert resp.status_code == 401


# ------------------------------------------------------------ flusso


class TestFlusso:
    async def test_decisione_ricorso_e_viste(self, posta):
        db, sec = await scenario_wp9()
        sid = await segnala_call(db, sec)
        coda = await chiama(db, "GET", f"{A}/segnalazioni")
        assert coda.status_code == 200
        corpo = coda.json()
        assert corpo["total"] == 1 and corpo["items"][0]["id"] == sid
        assert corpo["items"][0]["autore"] is None  # non ancora ricavata
        for nome in ("Y",):
            assert g.OWNER[nome] not in coda.text
        presa = await chiama(db, "POST", f"{A}/segnalazioni/{sid}/prendi")
        assert presa.json()["stato"] == "in_esame"
        assert presa.json()["autore"] == {"company_profile_id": g.COMPANY["X"],
                                          "ragione_sociale": RAGIONE["X"]}
        corta = await chiama(db, "POST", f"{A}/segnalazioni/{sid}/decidi",
                             json={"decisione": "call_sospesa", "motivazione": "troppo corta"})
        assert corta.status_code == 400
        assert corta.json()["error"]["code"] == "motivazione_non_valida"
        incoerente = await chiama(db, "POST", f"{A}/segnalazioni/{sid}/decidi",
                                  json={"decisione": "profilo_sospeso",
                                        "motivazione": MOTIVAZIONE})
        assert (incoerente.status_code, incoerente.json()["error"]["code"]) == (
            400, "decisione_non_valida")
        anteprima = await chiama(db, "POST", f"{A}/segnalazioni/{sid}/anteprima",
                                 json={"decisione": "call_sospesa", "motivazione": MOTIVAZIONE})
        assert anteprima.json()["versione"] == testi.SOR_VERSIONE
        assert anteprima.json()["testo"].startswith(testi.INTESTAZIONE_BOZZA)
        decisa = await chiama(db, "POST", f"{A}/segnalazioni/{sid}/decidi",
                              json={"decisione": "call_sospesa", "motivazione": MOTIVAZIONE})
        assert decisa.status_code == 200
        assert (decisa.json()["stato"], decisa.json()["effetto"]) == ("decisa", "applicato")
        assert "segnalante_user_id" not in decisa.text and ADMIN_ID not in decisa.text
        doppia = await chiama(db, "POST", f"{A}/segnalazioni/{sid}/decidi",
                              json={"decisione": "nessuna_azione", "motivazione": MOTIVAZIONE})
        assert (doppia.status_code, doppia.json()["error"]["code"]) == (
            409, "segnalazione_gia_decisa")
        # l'autore vede la decisione e lo statement, mai chi ha segnalato
        vista_x = await chiama(db, "GET", f"/api/v1/partenariati/segnalazioni/{sid}",
                               user=utente("X"), active=attiva("X"))
        assert vista_x.status_code == 200
        corpo_x = vista_x.json()
        assert (corpo_x["ruolo"], corpo_x["ricorso_possibile"]) == ("autore", True)
        assert corpo_x["sor_testo"].startswith(testi.INTESTAZIONE_BOZZA)
        assert corpo_x["descrizione"] is None
        canary(vista_x.text, "Y")
        assert DESCRIZIONE not in vista_x.text
        # chi ha segnalato vede l'esito, mai l'autore
        vista_y = await chiama(db, "GET", f"/api/v1/partenariati/segnalazioni/{sid}",
                               user=utente("Y"), active=attiva("Y"))
        assert (vista_y.json()["ruolo"], vista_y.json()["sor_testo"]) == ("segnalante", None)
        canary(vista_y.text, "X")
        estraneo = await chiama(db, "GET", f"/api/v1/partenariati/segnalazioni/{sid}",
                                user=utente("O"), active=attiva("O"))
        assert estraneo.status_code == 404
        # ricorso dell'autore (il membro: 403) e decisione dell'admin
        membro = await chiama(db, "POST", f"/api/v1/partenariati/segnalazioni/{sid}/ricorso",
                              user={"id": g.OWNER["X"], "role": "cliente"},
                              active=attiva("X", editable=False),
                              json={"testo": TESTO_RICORSO})
        assert membro.status_code == 403
        corto = await chiama(db, "POST", f"/api/v1/partenariati/segnalazioni/{sid}/ricorso",
                             user=utente("X"), active=attiva("X"), json={"testo": "no"})
        assert (corto.status_code, corto.json()["error"]["code"]) == (
            400, "ricorso_testo_non_valido")
        ricorso = await chiama(db, "POST", f"/api/v1/partenariati/segnalazioni/{sid}/ricorso",
                               user=utente("X"), active=attiva("X"),
                               json={"testo": TESTO_RICORSO})
        assert ricorso.status_code == 200
        assert ricorso.json()["ricorso"]["da"] == "autore"
        secondo = await chiama(db, "POST", f"/api/v1/partenariati/segnalazioni/{sid}/ricorso",
                               user=utente("X"), active=attiva("X"),
                               json={"testo": TESTO_RICORSO})
        assert (secondo.status_code, secondo.json()["error"]["code"]) == (
            409, "ricorso_non_ammesso")
        esito = await chiama(db, "POST", f"{A}/segnalazioni/{sid}/ricorso/decidi",
                             json={"esito": "riformata", "motivazione": MOTIVAZIONE_RICORSO})
        assert (esito.json()["stato"], esito.json()["effetto"]) == ("ricorso_deciso",
                                                                    "annullato")
        assert db.una("partner_calls", id=g.CALL_GUIDA_ID)["stato"] == "pubblicata"

    async def test_sospensione_diretta_e_call(self, posta):
        db, _ = await scenario_wp9()
        sospesa = await chiama(db, "POST", f"{A}/call/{g.CALL_GUIDA_ID}/sospendi",
                               json={"motivazione": MOTIVAZIONE})
        assert sospesa.status_code == 200
        assert sospesa.json() == {"oggetto_tipo": "call", "oggetto_id": g.CALL_GUIDA_ID,
                                  "esito": "applicato", "stato": "sospesa_moderazione",
                                  "modificato": True}
        elenco = await chiama(db, "GET", f"{A}/call", params={"stato": "sospesa_moderazione"})
        [riga] = elenco.json()["items"]
        assert (riga["id"], riga["sospeso_motivo"]) == (g.CALL_GUIDA_ID, MOTIVAZIONE)
        assert riga["creatore"]["ragione_sociale"] == RAGIONE["X"]
        ignoto = await chiama(db, "POST", f"{A}/boh/{g.CALL_GUIDA_ID}/sospendi",
                              json={"motivazione": MOTIVAZIONE})
        assert ignoto.status_code == 422
        ripristino = await chiama(db, "POST", f"{A}/call/{g.CALL_GUIDA_ID}/ripristina",
                                  json={"motivazione": MOTIVAZIONE})
        assert (ripristino.json()["stato"], ripristino.json()["modificato"]) == (
            "pubblicata", True)
        stato_errato = await chiama(db, "GET", f"{A}/call", params={"stato": "boh"})
        assert stato_errato.status_code == 422

    async def test_contesto_con_e_senza_motivazione(self, posta):
        from tests.test_partenariato_moderazione_service import (
            conversazione_lunga,
            segnalazione,
        )

        db, sec = await scenario_wp9()
        conv, righe = await conversazione_lunga(db, sec, n=25)
        sid = segnalazione(db, "messaggio", str(righe[12]["id"]), segnalante="X")
        finestra = await chiama(db, "GET", f"{A}/segnalazioni/{sid}/contesto")
        assert finestra.status_code == 200
        assert len(finestra.json()["messaggi"]) == 21
        canary(finestra.text, "X", "Y")
        # La conversazione intera solo con la POST e la motivazione nel corpo.
        for corpo in ({"motivazione": "corta"}, {"motivazione": "   "}):
            senza = await chiama(db, "POST", f"{A}/segnalazioni/{sid}/contesto", json=corpo)
            assert (senza.status_code, senza.json()["error"]["code"]) == (
                400, "motivazione_non_valida")
        assert (await chiama(db, "POST", f"{A}/segnalazioni/{sid}/contesto",
                             json={})).status_code == 422
        motivo = "Serve per decidere il ricorso presentato."
        intera = await chiama(db, "POST", f"{A}/segnalazioni/{sid}/contesto",
                              json={"motivazione": motivo})
        assert intera.status_code == 200 and len(intera.json()["messaggi"]) == 25
        assert intera.json()["completo"] is True
        [accesso] = [a for a in db.tabelle["audit_log"]
                     if a["action"] == "moderazione.contesto_completo"]
        assert accesso["payload"]["motivazione"] == motivo
        # La GET è solo la finestra: la motivazione (che descrive il caso) non
        # viaggia mai nell'URL, che finisce nei log di accesso.
        vecchia = await chiama(db, "GET", f"{A}/segnalazioni/{sid}/contesto",
                               params={"completo": "true", "motivazione": motivo})
        assert vecchia.status_code == 200 and vecchia.json()["completo"] is False
        assert len(vecchia.json()["messaggi"]) == 21
        assert len([a for a in db.tabelle["audit_log"]
                    if a["action"] == "moderazione.contesto_completo"]) == 1

    async def test_metriche_e_costi(self, posta):
        db, _ = await scenario_wp9()
        metriche = await chiama(db, "GET", f"{A}/metriche",
                                params={"da": "2026-01-01", "a": "2026-12-31"})
        assert metriche.status_code == 200
        assert (metriche.json()["da"], metriche.json()["a"]) == ("2026-01-01", "2026-12-31")
        assert set(metriche.json()["accettazione"]) == {"candidatura", "invito"}
        costi = await chiama(db, "GET", f"{A}/costi")
        assert costi.status_code == 200 and costi.json()["totali"] == []
        rovesciato = await chiama(db, "GET", f"{A}/costi",
                                  params={"da": "2026-03-02", "a": "2026-03-01"})
        assert (rovesciato.status_code, rovesciato.json()["error"]["code"]) == (
            400, "periodo_non_valido")
        malformata = await chiama(db, "GET", f"{A}/metriche", params={"da": "ieri"})
        assert malformata.status_code == 422

    async def test_identita(self, posta):
        db, _ = await scenario_wp9()
        db.richiedi_identita("X", nota="Preferisco la PEC")
        coda = await chiama(db, "GET", f"{A}/identita")
        [riga] = coda.json()["items"]
        assert (riga["company_profile_id"], riga["stato"], riga["nota"], riga["registro_ok"]) == (
            g.COMPANY["X"], "richiesta", "Preferisco la PEC", True)
        senza_metodo = await chiama(db, "POST", f"{A}/identita/{g.COMPANY['X']}/decidi",
                                    json={"esito": "verificata"})
        assert (senza_metodo.status_code, senza_metodo.json()["error"]["code"]) == (
            400, "metodo_obbligatorio")
        metodo_ignoto = await chiama(db, "POST", f"{A}/identita/{g.COMPANY['X']}/decidi",
                                     json={"esito": "verificata", "metodo": "piccione"})
        assert metodo_ignoto.status_code == 422
        verificata = await chiama(db, "POST", f"{A}/identita/{g.COMPANY['X']}/decidi",
                                  json={"esito": "verificata", "metodo": "pec"})
        assert verificata.status_code == 200
        assert (verificata.json()["stato"], verificata.json()["modificato"]) == (
            "verificata", True)
        revoca = await chiama(db, "POST", f"{A}/identita/{g.COMPANY['X']}/revoca",
                              json={"motivo": "Verifica ripetuta su richiesta"})
        assert (revoca.json()["stato"], revoca.json()["modificato"]) == ("non_richiesta", True)
        senza_motivo = await chiama(db, "POST", f"{A}/identita/{g.COMPANY['X']}/revoca",
                                    json={"motivo": " "})
        assert (senza_motivo.status_code, senza_motivo.json()["error"]["code"]) == (
            400, "motivo_obbligatorio")
        tutte = await chiama(db, "GET", f"{A}/identita", params={"stato": "tutte"})
        assert [r["stato"] for r in tutte.json()["items"]] == ["non_richiesta"]
        assert (await chiama(db, "GET", f"{A}/identita",
                             params={"stato": "verificata"})).json()["total"] == 0
        errato = await chiama(db, "GET", f"{A}/identita", params={"stato": "boh"})
        assert errato.status_code == 422
