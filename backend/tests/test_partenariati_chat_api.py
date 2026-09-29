"""API della chat dei partenariati (WP7): flag spento → 404 anche senza
token e con un corpo malformato; rotte registrate; invio (201, idempotente),
letture con cursore, «letto» (204), chiusura del creatore; membro in sola
lettura; canary cross-tenant (terzo owner, seconda azienda di un Advisor:
404; nei body mai id, P.IVA, nomi o email dell'altra azienda né dei suoi
utenti); segnalazione di un messaggio dell'altra azienda.

Mini-app con i router veri (`test_partenariati_candidature_api.mini_app`) e
i servizi veri sul primario finto del WP7."""

import copy
import uuid

import pytest

from app.core.config import get_settings
from tests.fixtures.partenariati import esempio_guida as g
from tests.test_partenariati_candidature_api import (
    _http,
    attiva,
    canary,
    chiama,
    corpo_candidatura,
)
from tests.test_partenariato_candidature_service import (  # noqa: F401 — fixture
    COMPANY_Y2,
    MEMBRO_X,
    fixture_fondo,
    pseudo,
    scenario_wp7,
)
from tests.test_partenariato_indice import (  # noqa: F401 — fixture autouse
    EMAIL,
    ambiente_wp6,
)

CONV_FINTA = "c1000000-0000-4000-8000-00000000abcd"
ROTTE = [
    ("GET", "/api/v1/partenariati/conversazioni"),
    ("GET", f"/api/v1/partenariati/conversazioni/{CONV_FINTA}"),
    ("GET", f"/api/v1/partenariati/conversazioni/{CONV_FINTA}/messaggi"),
    ("POST", f"/api/v1/partenariati/conversazioni/{CONV_FINTA}/messaggi"),
    ("POST", f"/api/v1/partenariati/conversazioni/{CONV_FINTA}/letto"),
    ("POST", f"/api/v1/partenariati/conversazioni/{CONV_FINTA}/chiudi"),
]
TESTO = "Buongiorno, possiamo sentirci domani? Il mio numero è 333 7654321."


async def aperta(fondo):
    """Y si candida e X accetta, tutto dalle API: la conversazione aperta."""
    db, sec = await scenario_wp7()
    cand = await chiama(db, sec, "Y", "POST", f"/partenariati/call/{g.CALL_GUIDA_ID}/candidature",
                        json=corpo_candidatura())
    assert cand.status_code == 201, cand.text
    accettata = await chiama(db, sec, "X", "POST",
                             f"/partenariati/candidature/{cand.json()['id']}/accetta")
    await fondo.azzera()
    return db, sec, accettata.json()["conversazione_id"]


async def scrivi(db, sec, nome, conv, testo=TESTO, chiave=None, **k):
    return await chiama(db, sec, nome, "POST", f"/partenariati/conversazioni/{conv}/messaggi",
                        json={"testo": testo, "client_msg_id": str(chiave or uuid.uuid4())}, **k)


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
        for percorso in ("/api/v1/partenariati/conversazioni",
                         "/api/v1/partenariati/conversazioni/{conversazione_id}",
                         "/api/v1/partenariati/conversazioni/{conversazione_id}/messaggi",
                         "/api/v1/partenariati/conversazioni/{conversazione_id}/letto",
                         "/api/v1/partenariati/conversazioni/{conversazione_id}/chiudi"):
            assert percorso in percorsi, percorso


class TestChat:
    async def test_invio_lettura_e_letto(self, fondo):
        db, sec, conv = await aperta(fondo)
        chiave = uuid.uuid4()
        primo = await scrivi(db, sec, "Y", conv, chiave=chiave)
        doppio = await scrivi(db, sec, "Y", conv, chiave=chiave)
        assert primo.status_code == doppio.status_code == 201
        assert primo.json()["id"] == doppio.json()["id"] and primo.json()["propria"] is True
        assert len(db.tabelle["partner_messaggi"]) == 1
        lista = await chiama(db, sec, "X", "GET", "/partenariati/conversazioni")
        [item] = lista.json()["items"]
        assert (item["non_letti"], item["lato"], item["controparte"]["pseudonimo"]) == (
            1, "creatore", pseudo("Y"))
        messaggi = await chiama(db, sec, "X", "GET", f"/partenariati/conversazioni/{conv}/messaggi")
        [m] = messaggi.json()["items"]
        assert (m["testo"], m["propria"], m["client_msg_id"]) == (TESTO, False, None)
        dopo = await chiama(db, sec, "X", "GET", f"/partenariati/conversazioni/{conv}/messaggi",
                            params={"dopo": m["id"]})
        assert dopo.json() == {"items": [], "ha_altri": False}
        letto = await chiama(db, sec, "X", "POST", f"/partenariati/conversazioni/{conv}/letto",
                             json={"fino_a_id": m["id"]})
        assert letto.status_code == 204
        dettaglio = await chiama(db, sec, "X", "GET", f"/partenariati/conversazioni/{conv}")
        assert dettaglio.json()["non_letti"] == 0 and dettaglio.json()["letto_fino_a_id"] == m["id"]
        assert dettaglio.json()["identita"] is None
        for testo in (lista.text, messaggi.text, dettaglio.text):
            canary(testo, "Y")
        # senza corpo «letto» vale fino all'ultimo
        vuoto = await chiama(db, sec, "Y", "POST", f"/partenariati/conversazioni/{conv}/letto")
        assert vuoto.status_code == 204

    async def test_validazioni(self, fondo):
        db, sec, conv = await aperta(fondo)
        for corpo in ({"testo": "   ", "client_msg_id": str(uuid.uuid4())},
                      {"testo": "x" * 5001, "client_msg_id": str(uuid.uuid4())}):
            resp = await chiama(db, sec, "Y", "POST",
                                f"/partenariati/conversazioni/{conv}/messaggi", json=corpo)
            assert resp.status_code == 400
        for corpo in ({"testo": "ciao"},
                      {"testo": "ciao", "client_msg_id": "x"},
                      {"testo": "ciao", "client_msg_id": str(uuid.uuid4()), "mittente": "X"}):
            resp = await chiama(db, sec, "Y", "POST",
                                f"/partenariati/conversazioni/{conv}/messaggi", json=corpo)
            assert resp.status_code == 422

    async def test_membro_chiusura_e_sola_lettura(self, fondo):
        db, sec, conv = await aperta(fondo)
        membro = attiva("X", editable=False)
        scritto = await scrivi(db, sec, "X", conv, active=membro, user={"id": MEMBRO_X})
        assert scritto.status_code == 403
        letto = await chiama(db, sec, "X", "POST", f"/partenariati/conversazioni/{conv}/letto",
                             active=membro, user={"id": MEMBRO_X})
        assert letto.status_code == 204
        chiusa_da_y = await chiama(db, sec, "Y", "POST",
                                   f"/partenariati/conversazioni/{conv}/chiudi")
        assert chiusa_da_y.status_code == 404
        chiusa = await chiama(db, sec, "X", "POST", f"/partenariati/conversazioni/{conv}/chiudi")
        assert chiusa.status_code == 200 and chiusa.json()["stato"] == "chiusa"
        dopo = await scrivi(db, sec, "Y", conv)
        assert dopo.status_code == 409
        assert dopo.json()["error"]["code"] == "conversazione_chiusa"

    async def test_isolamento_terzo_owner_e_advisor(self, fondo):
        db, sec, conv = await aperta(fondo)
        await scrivi(db, sec, "Y", conv)
        riga = copy.deepcopy(db.una("company_profiles", id=g.COMPANY["Y"]))
        riga.update(id=COMPANY_Y2, partita_iva="30000000001")
        db.tabelle["company_profiles"].append(riga)
        for nome, active in (("O", attiva("O")), ("Y", attiva("Y", company=COMPANY_Y2))):
            for metodo, percorso in (
                ("GET", f"/partenariati/conversazioni/{conv}"),
                ("GET", f"/partenariati/conversazioni/{conv}/messaggi"),
                ("POST", f"/partenariati/conversazioni/{conv}/letto"),
                ("POST", f"/partenariati/conversazioni/{conv}/chiudi"),
            ):
                resp = await chiama(db, sec, nome, metodo, percorso, active=active)
                assert resp.status_code == 404, (nome, percorso)
            assert (await scrivi(db, sec, nome, conv, active=active)).status_code == 404
            lista = await chiama(db, sec, nome, "GET", "/partenariati/conversazioni", active=active)
            assert lista.json()["items"] == []
        assert len(db.tabelle["partner_messaggi"]) == 1
        # Y non vede nulla di X e dei suoi utenti
        for percorso in ("/partenariati/conversazioni", f"/partenariati/conversazioni/{conv}",
                         f"/partenariati/conversazioni/{conv}/messaggi"):
            testo = (await chiama(db, sec, "Y", "GET", percorso)).text
            canary(testo, "X")
            assert MEMBRO_X not in testo and EMAIL["X"] not in testo

    async def test_segnalazione_di_un_messaggio(self, fondo):
        db, sec, conv = await aperta(fondo)
        messaggio = (await scrivi(db, sec, "Y", conv)).json()
        corpo = {"oggetto_tipo": "messaggio", "oggetto_id": str(messaggio["id"]),
                 "motivo": "contenuto_illecito", "descrizione": "Il messaggio contiene insulti",
                 "buona_fede": True}
        resp = await chiama(db, sec, "X", "POST", "/partenariati/segnalazioni", json=corpo)
        assert resp.status_code == 201, resp.text
        assert resp.json()["stato"] == "ricevuta"
        propria = await chiama(db, sec, "Y", "POST", "/partenariati/segnalazioni", json=corpo)
        estranea = await chiama(db, sec, "O", "POST", "/partenariati/segnalazioni", json=corpo)
        assert propria.status_code == estranea.status_code == 404
