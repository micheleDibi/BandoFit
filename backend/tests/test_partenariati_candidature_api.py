"""API delle candidature e degli inviti (WP7): flag spento → 404 anche senza
token e con un corpo malformato su ogni rotta nuova; rotte registrate; forme
e codici delle risposte; canary cross-tenant con tre owner (X crea la call,
Y si candida, O estraneo), Advisor con due aziende e membro in sola lettura:
nei body mai `company_profile_id`, owner, P.IVA, ragione sociale, email o
codice pubblico dell'altra parte né valori esatti di bilancio; rivelazione
spenta → nessuna identità e nessun audit di rivelazione, accesa (monkeypatch)
→ identità e audit.

Mini-app con i router veri e `dependency_overrides`; dietro, i servizi veri
sul primario finto del WP7 (`FakePrimaryWP7`) caricato con l'esempio guida."""

import copy
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI

from app.api import deps
from app.api.deps import ActiveCompany
from app.api.routers import (
    partenariati,
    partenariati_candidature,
    partenariati_chat,
    partenariati_scoperta,
    partner_calls,
)
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.services import partenariato_indice
from app.services import partner_profile_service as pps
from tests.fixtures.partenariati import esempio_guida as g
from tests.test_partenariato_candidature_service import (  # noqa: F401 — fixture
    COMPANY_Y2,
    MEMBRO_X,
    MESSAGGIO_Y,
    fixture_fondo,
    pseudo,
    scenario_wp7,
)
from tests.test_partenariato_indice import (  # noqa: F401 — fixture autouse
    EMAIL,
    PIVA,
    RAGIONE,
    ambiente_wp6,
)

CAND_FINTA = "a1000000-0000-4000-8000-00000000abcd"
CALL_FINTA = "e0000000-0000-4000-8000-00000000abcd"
# Valori esatti di bilancio (Y e X): mai verso l'altra parte. Il budget ESATTO
# della call (3,1 M€) invece lo vede la controparte accettata.
NUMERI_BILANCIO = ("2400000", "2600000", "812345", "3300000")
ROTTE = [
    ("POST", f"/api/v1/partenariati/call/{CALL_FINTA}/candidature"),
    ("POST", f"/api/v1/partenariati/call/{CALL_FINTA}/inviti"),
    ("GET", "/api/v1/partenariati/candidature"),
    ("GET", f"/api/v1/partenariati/candidature/{CAND_FINTA}"),
    ("POST", f"/api/v1/partenariati/candidature/{CAND_FINTA}/accetta"),
    ("POST", f"/api/v1/partenariati/candidature/{CAND_FINTA}/rifiuta"),
    ("POST", f"/api/v1/partenariati/candidature/{CAND_FINTA}/ritira"),
]


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
    for router in (partner_calls.router, partenariati_scoperta.router, partenariati.router,
                   partenariati_candidature.router, partenariati_chat.router):
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


def canary(testo: str, *nomi: str) -> None:
    for nome in nomi:
        for valore in (g.COMPANY[nome], g.OWNER[nome], PIVA[nome], RAGIONE[nome],
                       RAGIONE[nome].upper(), g.CODICE_PUBBLICO[nome], EMAIL[nome]):
            assert valore not in testo, (nome, valore)
    for numero in NUMERI_BILANCIO:
        assert numero not in testo, numero


def corpo_candidatura(**modifiche) -> dict:
    return {"posizione_id": g.POS_P1, "messaggio": MESSAGGIO_Y,
            "requisiti_dichiarati": [g.REQ["A"], g.REQ["C"]], **modifiche}


async def candida(db, sec, nome="Y", **modifiche) -> httpx.Response:
    return await chiama(db, sec, nome, "POST", f"/partenariati/call/{g.CALL_GUIDA_ID}/candidature",
                        json=corpo_candidatura(**modifiche))


# ------------------------------------------------------------ flag spento


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
        for percorso in ("/api/v1/partenariati/call/{call_id}/candidature",
                         "/api/v1/partenariati/call/{call_id}/inviti",
                         "/api/v1/partenariati/candidature",
                         "/api/v1/partenariati/candidature/{candidatura_id}",
                         "/api/v1/partenariati/candidature/{candidatura_id}/accetta",
                         "/api/v1/partenariati/candidature/{candidatura_id}/rifiuta",
                         "/api/v1/partenariati/candidature/{candidatura_id}/ritira"):
            assert percorso in percorsi, percorso


# ------------------------------------------------------------ flusso e forme


class TestFlusso:
    async def test_candidatura_accettazione_e_viste(self, fondo):
        db, sec = await scenario_wp7()
        resp = await candida(db, sec)
        assert resp.status_code == 201, resp.text
        corpo = resp.json()
        assert (corpo["lato"], corpo["stato"], corpo["quota"]) == (
            "partner", "inviata", {"usate": 1, "limite": 5})
        canary(resp.text, "X")
        # Y, prima dell'accettazione: la vista pubblica con la propria candidatura
        pubblica = await chiama(db, sec, "Y", "GET", f"/partenariati/call/{g.CALL_GUIDA_ID}")
        assert pubblica.status_code == 200
        assert pubblica.json()["candidatura"]["stato"] == "inviata"
        assert pubblica.json()["candidatura"]["puo_ritirare"] is True
        assert "vista" not in pubblica.json() and "dettagli_riservati" not in pubblica.json()
        assert pubblica.json()["match"]["spiegazione"] == g.SPIEGAZIONE_GUIDA
        canary(pubblica.text, "X")
        ricevute = await chiama(db, sec, "X", "GET", "/partenariati/candidature",
                                params={"direzione": "ricevute"})
        [item] = ricevute.json()["items"]
        per_call = await chiama(db, sec, "X", "GET", "/partenariati/candidature",
                                params={"direzione": "ricevute", "call_id": g.CALL_GUIDA_ID})
        altra = await chiama(db, sec, "X", "GET", "/partenariati/candidature",
                             params={"direzione": "ricevute", "call_id": g.CALL_ALTRA_ID})
        assert [i["id"] for i in per_call.json()["items"]] == [item["id"]]
        assert altra.json()["items"] == []
        assert item["candidato"]["pseudonimo"] == pseudo("Y")
        assert item["valutazione"]["copertura"] == {"coperti": 2, "cercati": 2}
        assert "punteggio" not in item["valutazione"]
        canary(ricevute.text, "Y")
        accettata = await chiama(db, sec, "X", "POST",
                                 f"/partenariati/candidature/{item['id']}/accetta")
        assert accettata.status_code == 200 and accettata.json()["stato"] == "accettata"
        conv = accettata.json()["conversazione_id"]
        canary(accettata.text, "Y")
        # Y: la call come controparte (riservati e budget esatto, nessuna identità)
        vista = await chiama(db, sec, "Y", "GET", f"/partenariati/call/{g.CALL_GUIDA_ID}")
        assert vista.status_code == 200
        corpo = vista.json()
        assert corpo["vista"] == "controparte" and corpo["identita"] is None
        assert corpo["dettagli_riservati"] == "CANARY-RISERVATO"
        assert corpo["candidatura"]["conversazione_id"] == conv
        canary(vista.text, "X")
        assert not [a for a in db.tabelle["audit_log"]
                    if a["action"] in ("partenariato.identita_rivelata",
                                       "partenariato.contatti_rivelati")]
        # la conversazione per le due parti
        assert (await chiama(db, sec, "Y", "GET", f"/partenariati/conversazioni/{conv}")) \
            .status_code == 200

    async def test_rivelazione_accesa(self, fondo, monkeypatch):
        monkeypatch.setattr(pps, "RIVELAZIONE_IDENTITA_DISPONIBILE", True)
        db, sec = await scenario_wp7()
        # Rivelazione simmetrica (WP9): entrambe verificate dalla piattaforma.
        db.verifica_identita(g.COMPANY["X"])
        db.verifica_identita(g.COMPANY["Y"])
        cand = (await candida(db, sec)).json()
        conv = (await chiama(db, sec, "X", "POST",
                             f"/partenariati/candidature/{cand['id']}/accetta")).json()[
            "conversazione_id"]
        azioni = [a["action"] for a in db.tabelle["audit_log"]]
        assert azioni.count("partenariato.identita_rivelata") == 1
        assert azioni.count("partenariato.contatti_rivelati") == 1
        vista = (await chiama(db, sec, "Y", "GET", f"/partenariati/call/{g.CALL_GUIDA_ID}")).json()
        assert vista["identita"]["ragione_sociale"] == RAGIONE["X"].upper()
        dettaglio = (await chiama(db, sec, "X", "GET", f"/partenariati/conversazioni/{conv}")) \
            .json()
        assert dettaglio["identita"]["ragione_sociale"] == RAGIONE["Y"].upper()
        assert EMAIL["X"] not in json.dumps(vista) and EMAIL["Y"] not in json.dumps(dettaglio)

    async def test_invito_con_pseudonimo_e_decisione_di_y(self, fondo):
        db, sec = await scenario_wp7()
        resp = await chiama(db, sec, "X", "POST", f"/partenariati/call/{g.CALL_GUIDA_ID}/inviti",
                            json={"pseudonimo": pseudo("Y")})
        assert resp.status_code == 201, resp.text
        assert resp.json()["inviti"] == {"attivi": 1, "massimo": 30}
        canary(resp.text, "Y")
        ricevuti = await chiama(db, sec, "Y", "GET", "/partenariati/candidature",
                                params={"direzione": "ricevute", "tipo": "invito"})
        [invito] = ricevuti.json()["items"]
        assert invito["puo_decidere"] is True and invito["candidato"] is None
        canary(ricevuti.text, "X")
        rifiutato = await chiama(db, sec, "Y", "POST",
                                 f"/partenariati/candidature/{invito['id']}/rifiuta",
                                 json={"motivo": "Non abbiamo disponibilità ora."})
        assert rifiutato.status_code == 200 and rifiutato.json()["stato"] == "rifiutata"

    async def test_body_con_company_profile_id_422_e_codici(self, fondo):
        db, sec = await scenario_wp7(Y=0)
        extra = await chiama(db, sec, "X", "POST",
                             f"/partenariati/call/{g.CALL_GUIDA_ID}/inviti",
                             json={"pseudonimo": pseudo("Y"), "company_profile_id": g.COMPANY["Y"]})
        assert extra.status_code == 422
        neutro = await chiama(db, sec, "X", "POST",
                              f"/partenariati/call/{g.CALL_GUIDA_ID}/inviti",
                              json={"pseudonimo": "AAAAAAAAAAAAAAAA"})
        assert neutro.status_code == 409
        assert neutro.json()["error"]["code"] == "partner_non_disponibile"
        gratuito = await candida(db, sec)
        assert gratuito.status_code == 409
        assert gratuito.json()["error"]["code"] == "funzione_non_inclusa"
        contatti = await candida(db, sec, nome="T", messaggio=(
            "Scrivete a lab@esempio.it per il laboratorio, i prototipi e le prove del gruppo."))
        assert contatti.status_code == 400
        assert contatti.json()["error"]["code"] == "testo_non_conforme"
        corto = await candida(db, sec, nome="T", messaggio="Breve")
        assert corto.status_code == 400

    async def test_lista_con_parametri_non_validi(self, fondo):
        db, sec = await scenario_wp7()
        for params in ({"direzione": "tutte"}, {"stato": "boh"}, {"page": 0},
                       {"call_id": "non-un-id"}):
            resp = await chiama(db, sec, "X", "GET", "/partenariati/candidature", params=params)
            assert resp.status_code == 422


# ------------------------------------------------------------ canary cross-tenant


class TestIsolamento:
    async def _con_advisor(self):
        """X ha una candidatura di Y; il titolare di Y (Advisor) ha anche Y2."""
        db, sec = await scenario_wp7()
        riga = copy.deepcopy(db.una("company_profiles", id=g.COMPANY["Y"]))
        riga.update(id=COMPANY_Y2, ragione_sociale="Seconda Sintetica Srl",
                    partita_iva="30000000001")
        db.tabelle["company_profiles"].append(riga)
        cand = (await candida(db, sec)).json()
        return db, sec, cand["id"]

    async def test_estraneo_advisor_e_membro(self, fondo):
        db, sec, cid = await self._con_advisor()
        y2 = attiva("Y", company=COMPANY_Y2)
        membro = attiva("X", editable=False)
        casi = [
            ("O", attiva("O"), utente("O")),  # terzo owner
            ("Y", y2, utente("Y")),  # stessa persona, altra azienda attiva
        ]
        for nome, active, user in casi:
            dettaglio = await chiama(db, sec, nome, "GET", f"/partenariati/candidature/{cid}",
                                     active=active, user=user)
            assert dettaglio.status_code == 404, nome
            for azione in ("accetta", "rifiuta", "ritira"):
                resp = await chiama(db, sec, nome, "POST",
                                    f"/partenariati/candidature/{cid}/{azione}",
                                    active=active, user=user)
                assert resp.status_code == 404, (nome, azione)
            for direzione in ("inviate", "ricevute"):
                lista = await chiama(db, sec, nome, "GET", "/partenariati/candidature",
                                     params={"direzione": direzione}, active=active, user=user)
                assert lista.json()["items"] == [], (nome, direzione)
        # il membro di X legge, non decide
        letta = await chiama(db, sec, "X", "GET", f"/partenariati/candidature/{cid}",
                             active=membro, user={"id": MEMBRO_X})
        assert letta.status_code == 200 and letta.json()["puo_decidere"] is False
        canary(letta.text, "Y")
        vietata = await chiama(db, sec, "X", "POST", f"/partenariati/candidature/{cid}/accetta",
                               active=membro, user={"id": MEMBRO_X})
        assert vietata.status_code == 403
        invito = await chiama(db, sec, "X", "POST",
                              f"/partenariati/call/{g.CALL_GUIDA_ID}/inviti",
                              json={"pseudonimo": pseudo("T")}, active=membro,
                              user={"id": MEMBRO_X})
        assert invito.status_code == 403
        # nessuna scrittura da chi non poteva
        assert [r["stato"] for r in db.tabelle["partner_candidature"]] == ["inviata"]

    async def test_nessun_id_di_terzi_nei_body(self, fondo):
        db, sec, cid = await self._con_advisor()
        await chiama(db, sec, "X", "POST", f"/partenariati/call/{g.CALL_GUIDA_ID}/inviti",
                     json={"pseudonimo": pseudo("T")})
        for nome, altri in (("X", ("Y", "T", "O")), ("Y", ("X", "T", "O")),
                            ("T", ("X", "Y", "O"))):
            testi = []
            for direzione in ("inviate", "ricevute"):
                testi.append((await chiama(db, sec, nome, "GET", "/partenariati/candidature",
                                           params={"direzione": direzione})).text)
            for riga in db.tabelle["partner_candidature"]:
                testi.append((await chiama(db, sec, nome, "GET",
                                           f"/partenariati/candidature/{riga['id']}")).text)
            if nome != "X":  # la vista del creatore è del WP5 (tutto suo)
                testi.append((await chiama(db, sec, nome, "GET",
                                           f"/partenariati/call/{g.CALL_GUIDA_ID}")).text)
            for testo in testi:
                canary(testo, *altri)
                assert "family_parent_id" not in testo and "inviata_da_user_id" not in testo
                assert "company_profile_id" not in testo or nome == "X"


# ------------------------------------------------------------ accesso alla call


class TestAccessoAllaCall:
    """`invitato` e `candidato` valgono solo per una riga IN ATTESA: una riga
    chiusa non apre più una call solo su invito; la controparte accettata non
    legge una call sospesa per moderazione."""

    async def _invito_su_call_riservata(self, db, sec) -> str:
        db.una("partner_calls", id=g.CALL_GUIDA_ID)["visibilita"] = "solo_invitati"
        partenariato_indice.invalida()
        resp = await chiama(db, sec, "X", "POST", f"/partenariati/call/{g.CALL_GUIDA_ID}/inviti",
                            json={"pseudonimo": pseudo("Y")})
        assert resp.status_code == 201, resp.text
        return resp.json()["id"]

    @pytest.mark.parametrize("chiusura", ["ritirato", "rifiutato", "ttl", "opt_out"])
    async def test_invito_chiuso_non_apre_la_call_solo_su_invito(self, fondo, chiusura):
        db, sec = await scenario_wp7()
        invito = await self._invito_su_call_riservata(db, sec)
        in_attesa = await chiama(db, sec, "Y", "GET", f"/partenariati/call/{g.CALL_GUIDA_ID}")
        assert in_attesa.status_code == 200
        assert in_attesa.json()["candidatura"]["stato"] == "inviata"
        riga = db.una("partner_candidature", id=invito)
        if chiusura == "ritirato":
            resp = await chiama(db, sec, "X", "POST", f"/partenariati/candidature/{invito}/ritira")
            assert resp.status_code == 200
        elif chiusura == "rifiutato":
            resp = await chiama(db, sec, "Y", "POST",
                                f"/partenariati/candidature/{invito}/rifiuta")
            assert resp.status_code == 200
        elif chiusura == "ttl":  # scaduto ma non ancora marcato: vale già come chiuso
            riga["scade_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        else:  # revoca dell'opt-in (lo fa il trigger della 0039)
            riga.update(stato="scaduta", motivo_chiusura="opt_out",
                        chiusa_at=datetime.now(timezone.utc).isoformat())
        # una modifica successiva della call non deve arrivare a Y
        db.una("partner_calls", id=g.CALL_GUIDA_ID)["titolo"] = "Titolo cambiato dopo"
        dopo = await chiama(db, sec, "Y", "GET", f"/partenariati/call/{g.CALL_GUIDA_ID}")
        assert dopo.status_code == 404
        assert "Titolo cambiato" not in dopo.text
        segnala = await chiama(db, sec, "Y", "POST", "/partenariati/segnalazioni", json={
            "oggetto_tipo": "call", "oggetto_id": g.CALL_GUIDA_ID,
            "motivo": "contenuto_illecito", "descrizione": "Prova di accesso dopo la chiusura.",
            "buona_fede": True})
        assert segnala.status_code == 404

    async def test_candidatura_chiusa_su_call_pubblica_resta_con_la_call(self, fondo):
        db, sec = await scenario_wp7()
        cand = (await candida(db, sec)).json()
        await chiama(db, sec, "X", "POST", f"/partenariati/candidature/{cand['id']}/rifiuta")
        vista = await chiama(db, sec, "Y", "GET", f"/partenariati/call/{g.CALL_GUIDA_ID}")
        assert vista.status_code == 200
        assert vista.json()["candidatura"]["stato"] == "rifiutata"
        assert vista.json()["candidatura"]["chiusa_at"] is None  # decisa, non chiusa
        # la stessa call non più visibile a tutti: 404 anche per chi si era candidato
        db.una("partner_calls", id=g.CALL_GUIDA_ID)["visibilita"] = "solo_invitati"
        nascosta = await chiama(db, sec, "Y", "GET", f"/partenariati/call/{g.CALL_GUIDA_ID}")
        assert nascosta.status_code == 404

    async def test_ritirata_mostra_la_data(self, fondo):
        db, sec = await scenario_wp7()
        cand = (await candida(db, sec)).json()
        await chiama(db, sec, "Y", "POST", f"/partenariati/candidature/{cand['id']}/ritira")
        vista = (await chiama(db, sec, "Y", "GET", f"/partenariati/call/{g.CALL_GUIDA_ID}")).json()
        assert vista["candidatura"]["stato"] == "ritirata"
        assert vista["candidatura"]["chiusa_at"] is not None

    async def test_controparte_non_legge_una_call_sospesa(self, fondo):
        db, sec = await scenario_wp7()
        cand = (await candida(db, sec)).json()
        conv = (await chiama(db, sec, "X", "POST",
                             f"/partenariati/candidature/{cand['id']}/accetta")).json()[
            "conversazione_id"]
        db.una("partner_calls", id=g.CALL_GUIDA_ID).update(
            stato="sospesa_moderazione", sospesa_at=datetime.now(timezone.utc).isoformat(),
            stato_prima_sospensione="pubblicata")
        vista = await chiama(db, sec, "Y", "GET", f"/partenariati/call/{g.CALL_GUIDA_ID}")
        assert vista.status_code == 404
        assert "CANARY-RISERVATO" not in vista.text
        # il creatore la vede ancora; la conversazione resta raggiungibile dal suo id
        assert (await chiama(db, sec, "X", "GET", f"/partenariati/call/{g.CALL_GUIDA_ID}")) \
            .status_code == 200
        assert (await chiama(db, sec, "Y", "GET", f"/partenariati/conversazioni/{conv}")) \
            .status_code == 200
