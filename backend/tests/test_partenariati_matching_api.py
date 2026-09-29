"""API del matching dei partenariati (WP6): flag spento → 404 anche senza
token su ogni rotta nuova, tranne la disiscrizione pubblica; «Per te» (anche
senza opt-in), bacheca con filtri e ordinamenti, dettaglio pubblico con il
proprio match, suggeriti del creatore con pseudonimi, salva/segui,
riepilogo, impostazioni email e disiscrizione per token. Canary: nei body
nessun id interno, P.IVA, ragione sociale, riservato o numero esatto di
bilancio di un'altra azienda. Advisor con due aziende; membro in sola
lettura.

Mini-app con i router veri e `dependency_overrides`; dietro, i servizi veri
sul primario finto di `test_partenariato_indice` caricato con l'esempio
guida."""

import copy
import json
import re

import httpx
import pytest
from fastapi import FastAPI

from app.api import deps
from app.api.deps import ActiveCompany
from app.api.routers import partenariati_email, partenariati_scoperta, partner_calls
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.services import partenariato_accesso, partenariato_indice
from tests.fixtures.partenariati import esempio_guida as g
from tests.test_partenariato_indice import (  # noqa: F401 — fixture autouse
    PIVA,
    RAGIONE,
    T0,
    ambiente_wp6,
    scenario_guida,
)

CALL_FINTA = "e0000000-0000-4000-8000-00000000abcd"
ROTTE_NUOVE = [
    ("GET", "/api/v1/partenariati/per-te"),
    ("GET", "/api/v1/partenariati/riepilogo"),
    ("GET", "/api/v1/me/partenariati/email-settings"),
    ("PUT", "/api/v1/me/partenariati/email-settings"),
    ("GET", "/api/v1/partenariati/call?vista=tutte"),
    ("GET", "/api/v1/partenariati/call?vista=salvate"),
    ("GET", f"/api/v1/partenariati/call/{CALL_FINTA}/suggeriti"),
    ("GET", f"/api/v1/partenariati/call/{CALL_FINTA}/match"),
    ("POST", f"/api/v1/partenariati/call/{CALL_FINTA}/salva"),
    ("DELETE", f"/api/v1/partenariati/call/{CALL_FINTA}/salva"),
]
# Valori esatti di bilancio delle aziende dell'esempio (mai verso terzi).
NUMERI_ESATTI = ("2400000", "2600000", "812345", "3100000", "3300000", "1500000")
MEMBRO_X = "b0000000-0000-4000-8000-0000000000aa"
COMPANY_Y2 = "c0000000-0000-4000-8000-0000000000y2".replace("y2", "22")
CALL_Y2 = "e0000000-0000-4000-8000-000000000022"


def utente(nome: str) -> dict:
    return {"id": g.OWNER[nome], "role": "cliente", "is_active": True}


def attiva(nome: str, *, editable: bool = True, company: str | None = None) -> ActiveCompany:
    return ActiveCompany(company_id=company or g.COMPANY[nome], owner_id=g.OWNER[nome],
                         editable=editable)


def _http(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def mini_app(db, sec, *, user: dict | None, active: ActiveCompany) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    for router in (partner_calls.router, partenariati_scoperta.router,
                   partenariati_email.router):
        app.include_router(router, prefix="/api/v1")
    if user is not None:
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


def canary_di(*nomi: str) -> list[str]:
    valori = []
    for nome in nomi:
        valori += [g.COMPANY[nome], g.OWNER[nome], PIVA[nome], RAGIONE[nome],
                   RAGIONE[nome].upper(), g.CODICE_PUBBLICO[nome]]
    return valori + ["CANARY-RISERVATO", *NUMERI_ESATTI]


def senza_canary(testo: str, *nomi: str) -> None:
    for canary in canary_di(*nomi):
        assert canary not in testo, canary


# ------------------------------------------------------------ flag spento


class TestFlagSpento:
    @pytest.fixture
    def spento(self, monkeypatch):
        monkeypatch.setenv("PARTENARIATI_ATTIVO", "false")
        get_settings.cache_clear()

    @pytest.mark.parametrize(("metodo", "percorso"), ROTTE_NUOVE)
    async def test_404_senza_token_sull_app_vera(self, spento, metodo, percorso):
        from app.main import app

        async with _http(app) as client:
            resp = await client.request(metodo, percorso, content=b"{malformato")
        assert resp.status_code == 404
        assert resp.json() == {"error": {"code": "not_found", "message": "Risorsa non trovata"}}

    async def test_la_disiscrizione_resta_attiva(self, spento):
        from app.main import app

        db, _ = await scenario_guida()
        riga = db.inserisci("partner_email_settings", {"user_id": g.OWNER["Y"]})
        app.dependency_overrides[deps.get_primary] = lambda: db
        try:
            async with _http(app) as client:
                pagina = await client.get(
                    "/api/v1/partenariati/email/unsubscribe",
                    params={"token": riga["unsubscribe_token"], "tipo": "digest"})
                assert db.una("partner_email_settings", user_id=g.OWNER["Y"])[
                    "digest_abilitato"] is True  # il GET non muta
                post = await client.post(
                    "/api/v1/partenariati/email/unsubscribe",
                    params={"token": riga["unsubscribe_token"], "tipo": "digest"})
        finally:
            app.dependency_overrides.pop(deps.get_primary, None)
        assert pagina.status_code == 200 and "<form method=\"post\"" in pagina.text
        assert post.status_code == 204
        assert db.una("partner_email_settings", user_id=g.OWNER["Y"])["digest_abilitato"] is False

    async def test_rotte_registrate_nell_app(self, monkeypatch):
        monkeypatch.setenv("PARTENARIATI_ATTIVO", "true")
        get_settings.cache_clear()
        from app.main import app

        percorsi = app.openapi()["paths"]
        for percorso in ("/api/v1/partenariati/per-te", "/api/v1/partenariati/riepilogo",
                         "/api/v1/me/partenariati/email-settings",
                         "/api/v1/partenariati/email/unsubscribe",
                         "/api/v1/partenariati/call/{call_id}/suggeriti",
                         "/api/v1/partenariati/call/{call_id}/match",
                         "/api/v1/partenariati/call/{call_id}/salva"):
            assert percorso in percorsi, percorso


# ------------------------------------------------------------ «Per te»


class TestPerTe:
    async def test_per_te_di_y(self):
        db, sec = await scenario_guida()
        resp = await chiama(db, sec, "Y", "GET", "/partenariati/per-te")
        assert resp.status_code == 200
        corpo = resp.json()
        assert set(corpo) == {"items", "total", "page", "page_size", "total_pages", "opt_in"}
        assert corpo["opt_in"] is True and corpo["total"] == 2
        primo = corpo["items"][0]
        assert primo["id"] == g.CALL_GUIDA_ID
        assert primo["match"]["spiegazione"] == g.SPIEGAZIONE_GUIDA
        assert primo["match"]["copertura"] == {"coperti": 2, "cercati": 2}
        assert "punteggio" not in primo["match"]  # oracolo sui dati del creatore
        assert primo["creatore"]["denominazione"] == "Azienda anonima"
        assert primo["creatore"]["regione"] == "Calabria"
        assert primo["mia"] is False and primo["posti"] == 1 and primo["salvata"] is False
        assert g.CALL_RISERVATA_ID not in resp.text
        senza_canary(resp.text, "X", "O")

    async def test_per_te_senza_opt_in(self):
        db, sec = await scenario_guida()
        resp = await chiama(db, sec, "V", "GET", "/partenariati/per-te")
        corpo = resp.json()
        assert resp.status_code == 200 and corpo["opt_in"] is False
        assert [i["id"] for i in corpo["items"]] == [g.CALL_GUIDA_ID, g.CALL_ALTRA_ID]

    async def test_senza_azienda_409(self):
        db, sec = await scenario_guida()
        senza = ActiveCompany(company_id=None, owner_id=g.OWNER["Y"], editable=True)
        resp = await chiama(db, sec, "Y", "GET", "/partenariati/per-te", active=senza)
        assert resp.status_code == 409 and resp.json()["error"]["code"] == "azienda_mancante"

    async def test_membro_legge(self):
        db, sec = await scenario_guida()
        resp = await chiama(db, sec, "Y", "GET", "/partenariati/per-te",
                            active=attiva("Y", editable=False))
        assert resp.status_code == 200 and resp.json()["items"]


# ------------------------------------------------------------ bacheca


class TestBacheca:
    async def test_tutte_con_il_proprio_match(self):
        db, sec = await scenario_guida()
        resp = await chiama(db, sec, "Y", "GET", "/partenariati/call?vista=tutte")
        corpo = resp.json()
        assert resp.status_code == 200
        assert [i["id"] for i in corpo["items"]] == [g.CALL_GUIDA_ID, g.CALL_ALTRA_ID]
        assert corpo["items"][0]["match"]["spiegazione"] == g.SPIEGAZIONE_GUIDA
        assert corpo["items"][0]["candidature_ricevute"] == 0
        senza_canary(resp.text, "X", "O")

    async def test_le_proprie_call_non_sono_in_bacheca(self):
        db, sec = await scenario_guida()
        resp = await chiama(db, sec, "X", "GET", "/partenariati/call?vista=tutte")
        ids = [i["id"] for i in resp.json()["items"]]
        assert g.CALL_GUIDA_ID not in ids and g.CALL_ALTRA_ID in ids

    async def test_senza_azienda_niente_match(self):
        db, sec = await scenario_guida()
        senza = ActiveCompany(company_id=None, owner_id=g.OWNER["V"], editable=True)
        resp = await chiama(db, sec, "V", "GET", "/partenariati/call?vista=tutte&ordine=recenti",
                            active=senza)
        assert resp.status_code == 200
        assert {i["match"] is None for i in resp.json()["items"]} == {True}

    @pytest.mark.parametrize(
        ("filtro", "attesi"),
        [
            ("bando=bando-sintetico-ricerca-sviluppo", [g.CALL_GUIDA_ID]),
            (f"bando={g.BANDO_ALTRO}", [g.CALL_ALTRA_ID]),
            (f"regione={g.CALABRIA}", [g.CALL_GUIDA_ID]),
            (f"regione={g.LOMBARDIA}", [g.CALL_ALTRA_ID]),
            ("forma=ats", [g.CALL_GUIDA_ID]),
            ("ruolo=capofila", []),
            ("ruolo=partner", [g.CALL_GUIDA_ID, g.CALL_ALTRA_ID]),
        ],
    )
    async def test_filtri(self, filtro, attesi):
        db, sec = await scenario_guida()
        resp = await chiama(db, sec, "Y", "GET", f"/partenariati/call?vista=tutte&{filtro}")
        assert [i["id"] for i in resp.json()["items"]] == attesi

    async def test_ordinamenti(self):
        db, sec = await scenario_guida()
        db.una("partner_calls", id=g.CALL_ALTRA_ID).update(
            pubblicata_at="2026-10-02T09:00:00+00:00", scadenza_call="2026-11-01")
        recenti = await chiama(db, sec, "Y", "GET", "/partenariati/call?vista=tutte&ordine=recenti")
        partenariato_indice.invalida()
        scadenza = await chiama(db, sec, "Y", "GET",
                                "/partenariati/call?vista=tutte&ordine=scadenza")
        affinita = await chiama(db, sec, "Y", "GET", "/partenariati/call?vista=tutte")
        assert [i["id"] for i in recenti.json()["items"]] == [g.CALL_ALTRA_ID, g.CALL_GUIDA_ID]
        assert [i["id"] for i in scadenza.json()["items"]] == [g.CALL_ALTRA_ID, g.CALL_GUIDA_ID]
        assert [i["id"] for i in affinita.json()["items"]] == [g.CALL_GUIDA_ID, g.CALL_ALTRA_ID]

    async def test_una_call_chiusa_sparisce_subito_anche_con_indice_fresco(self):
        db, sec = await scenario_guida()
        await chiama(db, sec, "Y", "GET", "/partenariati/call?vista=tutte")
        db.una("partner_calls", id=g.CALL_ALTRA_ID)["stato"] = "chiusa_annullata"
        resp = await chiama(db, sec, "Y", "GET", "/partenariati/call?vista=tutte")
        assert [i["id"] for i in resp.json()["items"]] == [g.CALL_GUIDA_ID]

    async def test_mie_con_i_contatori(self):
        db, sec = await scenario_guida()
        resp = await chiama(db, sec, "X", "GET", "/partenariati/call?vista=mie")
        [mia] = resp.json()["items"]
        assert mia["id"] == g.CALL_GUIDA_ID and mia["mia"] is True
        assert mia["match"] is None and mia["posti"] == 1


# ------------------------------------------------------ dettaglio e match


class TestDettaglioPubblico:
    async def test_vista_pubblica_con_il_proprio_match(self):
        db, sec = await scenario_guida()
        resp = await chiama(db, sec, "Y", "GET", f"/partenariati/call/{g.CALL_GUIDA_ID}")
        corpo = resp.json()
        assert resp.status_code == 200 and "editable" not in corpo
        assert corpo["match"]["spiegazione"] == g.SPIEGAZIONE_GUIDA
        assert "punteggio" not in corpo["match"]
        # vista «proprio»: i PROPRI valori (fatturato medio di Y, 2,5 M€)
        [riga] = corpo["match"]["dettaglio"]
        assert riga.startswith("«F»:") and "2.500.000" in riga
        assert (corpo["salvata"], corpo["opt_in"]) == (False, True)
        assert corpo["creatore"]["denominazione"] == "Azienda anonima"
        senza_canary(resp.text, "X")
        # ... che non escono mai nelle liste né verso il creatore
        bacheca = await chiama(db, sec, "Y", "GET", "/partenariati/call?vista=tutte")
        suggeriti = await chiama(db, sec, "X", "GET",
                                 f"/partenariati/call/{g.CALL_GUIDA_ID}/suggeriti")
        assert "2.500.000" not in bacheca.text and "2.500.000" not in suggeriti.text

    async def test_il_dettaglio_proprio_mostra_solo_i_propri_valori(self):
        db, sec = await scenario_guida()
        resp = await chiama(db, sec, "U", "GET", f"/partenariati/call/{g.CALL_GUIDA_ID}")
        corpo = resp.json()
        # U non è compatibile (regola finanziaria): nessun match, nessun numero
        assert resp.status_code == 200 and corpo["match"] is None
        senza_canary(resp.text, "X", "Y")

    async def test_match(self):
        db, sec = await scenario_guida()
        y = await chiama(db, sec, "Y", "GET", f"/partenariati/call/{g.CALL_GUIDA_ID}/match")
        x = await chiama(db, sec, "X", "GET", f"/partenariati/call/{g.CALL_GUIDA_ID}/match")
        assert y.status_code == 200 and y.json()["copertura"] == {"coperti": 2, "cercati": 2}
        assert x.status_code == 200 and x.json() is None
        senza_canary(y.text, "X")

    async def test_call_solo_su_invito_404(self):
        db, sec = await scenario_guida()
        for percorso in (f"/partenariati/call/{g.CALL_RISERVATA_ID}",
                         f"/partenariati/call/{g.CALL_RISERVATA_ID}/match"):
            resp = await chiama(db, sec, "Y", "GET", percorso)
            assert resp.status_code == 404

    async def test_call_sospesa_non_e_pubblica(self):
        db, sec = await scenario_guida()
        db.una("partner_calls", id=g.CALL_ALTRA_ID)["sospesa_at"] = T0
        dettaglio = await chiama(db, sec, "Y", "GET", f"/partenariati/call/{g.CALL_ALTRA_ID}")
        partenariato_indice.invalida()
        bacheca = await chiama(db, sec, "Y", "GET", "/partenariati/call?vista=tutte")
        assert dettaglio.status_code == 404
        assert g.CALL_ALTRA_ID not in bacheca.text

    async def test_l_admin_e_un_visitatore_come_gli_altri(self):
        db, sec = await scenario_guida()
        admin = {"id": g.OWNER["Y"], "role": "admin", "is_active": True}
        pubblica = await chiama(db, sec, "Y", "GET", f"/partenariati/call/{g.CALL_GUIDA_ID}",
                                user=admin)
        riservata = await chiama(db, sec, "Y", "GET",
                                 f"/partenariati/call/{g.CALL_RISERVATA_ID}", user=admin)
        assert pubblica.status_code == 200 and "editable" not in pubblica.json()
        assert riservata.status_code == 404
        senza_canary(pubblica.text, "X")

    async def test_call_scaduta_ma_non_chiusa_404_senza_scritture(self):
        db, sec = await scenario_guida()
        db.una("partner_calls", id=g.CALL_ALTRA_ID)["scadenza_call"] = "2026-10-01"
        db.ops.clear()
        resp = await chiama(db, sec, "Y", "GET", f"/partenariati/call/{g.CALL_ALTRA_ID}")
        assert resp.status_code == 404
        assert [o for o in db.ops if o["op"] != "select"] == []


# ------------------------------------------------------------ suggeriti


class TestSuggeriti:
    async def test_suggeriti_del_creatore_con_pseudonimi(self):
        db, sec = await scenario_guida()
        db.una("company_partner_profiles", company_profile_id=g.COMPANY["Y"])[
            "descrizione_competenze"] = (
            "Laboratorio della Impresa Sintetica Y dedicato a prototipi e prove di dispositivi "
            "medicali per le imprese del territorio.")
        resp = await chiama(db, sec, "X", "GET", f"/partenariati/call/{g.CALL_GUIDA_ID}/suggeriti")
        corpo = resp.json()
        assert resp.status_code == 200 and corpo["total"] == 2
        y, t = corpo["items"]
        assert set(y) == {"pseudonimo", "profilo", "match", "stato_contatto"}
        assert y["stato_contatto"] is None  # nessun contatto ancora (WP7)
        assert re.fullmatch(r"[A-Z2-7]{16}", y["pseudonimo"])
        profilo = y["profilo"]
        assert "codice_pubblico" not in profilo  # handle stabile: mai al creatore
        assert (profilo["denominazione"], profilo["anonimo"], profilo["regione_sede"]) == (
            None, True, "Lazio")
        assert profilo["classe_dimensionale"] == "piccola"
        assert profilo["fasce"] == {"fatturato": "2m_10m", "patrimonio_netto": None,
                                    "dipendenti": None, "trend": None}
        assert [c["codice"] for c in profilo["competenze"]] == [
            "prototipazione_testing", "ricerca_industriale", "dispositivi_medici_salute"]
        assert profilo["esperienze"] == [{"programma": "Horizon Europe", "anno": None,
                                          "ruolo": None, "titolo": None}]
        assert profilo["accetta_inviti"] is True
        # testi liberi di un anonimo senza gli identificativi dell'azienda
        assert "Laboratorio della" in profilo["descrizione_competenze"]
        assert "Sintetica Y" not in resp.text
        assert y["match"]["spiegazione"] == g.SPIEGAZIONE_GUIDA
        assert y["match"]["fasce"] == {"fatturato": "2m_10m", "patrimonio_netto": None,
                                       "dipendenti": None, "trend": None}
        assert y["match"]["dettaglio"] is None
        assert "punteggio" not in y["match"]  # esposizioni e sedi del candidato
        assert t["match"]["attenzione"][0]["codice"] == "bilanci_non_disponibili"
        senza_canary(resp.text, "Y", "T", "Z", "W", "V", "U")

    async def test_membro_legge_gli_altri_no(self):
        db, sec = await scenario_guida()
        membro = await chiama(db, sec, "X", "GET",
                              f"/partenariati/call/{g.CALL_GUIDA_ID}/suggeriti",
                              active=attiva("X", editable=False))
        altro = await chiama(db, sec, "Y", "GET",
                             f"/partenariati/call/{g.CALL_GUIDA_ID}/suggeriti")
        assert membro.status_code == 200 and len(membro.json()["items"]) == 2
        assert altro.status_code == 404

    async def test_revoca_di_y_sparisce_subito_con_indice_fresco(self):
        db, sec = await scenario_guida()
        percorso = f"/partenariati/call/{g.CALL_GUIDA_ID}/suggeriti"
        prima = await chiama(db, sec, "X", "GET", percorso)
        assert len(prima.json()["items"]) == 2
        db.una("company_partner_profiles", company_profile_id=g.COMPANY["Y"])[
            "visibile_come_partner"] = False
        dopo = await chiama(db, sec, "X", "GET", percorso)
        assert len(dopo.json()["items"]) == 1 and dopo.json()["total"] == 1
        assert dopo.json()["items"][0]["pseudonimo"] == prima.json()["items"][1]["pseudonimo"]

    async def test_filtro_per_posizione(self):
        db, sec = await scenario_guida()
        resp = await chiama(db, sec, "X", "GET",
                            f"/partenariati/call/{g.CALL_GUIDA_ID}/suggeriti?posizione=nessuna")
        assert resp.json()["items"] == []

    async def test_al_massimo_due_aziende_dello_stesso_owner_su_tutta_la_lista(self):
        """Il titolare di Y ha altre due aziende uguali a Y: al creatore ne
        arrivano due in tutto (non due per pagina), e il totale non conta la
        terza, nemmeno con il filtro per posizione."""
        db, sec = await scenario_guida()
        copie = {}
        for n in (1, 2):
            company = f"c0000000-0000-4000-8000-0000000009{n:02d}"
            codice = f"d0000000-0000-4000-8000-0000000009{n:02d}"
            piva = f"3000000009{n}"
            for tabella, campo in (("company_profiles", "id"), ("company_data",
                                   "company_profile_id"), ("company_partner_profiles",
                                   "company_profile_id"), ("company_financials",
                                   "company_profile_id"), ("company_financials_stato",
                                   "company_profile_id")):
                for riga in [r for r in db.tabelle[tabella] if r[campo] == g.COMPANY["Y"]]:
                    nuova = copy.deepcopy(riga)
                    nuova[campo] = company
                    db.tabelle[tabella].append(nuova)
            db.una("company_profiles", id=company).update(
                ragione_sociale=f"Copia {n} Sintetica Srl", partita_iva=piva,
                codice_fiscale=piva)
            dati = db.una("company_data", company_profile_id=company)
            dati.update(piva_fetched=piva, denominazione=f"COPIA {n} SINTETICA SRL")
            dati["raw"]["companyDetails"].update(companyName=f"COPIA {n} SINTETICA SRL",
                                                 vatCode=piva, taxCode=piva)
            db.una("company_partner_profiles", company_profile_id=company)[
                "codice_pubblico"] = codice
            copie[company] = codice
        from app.services import partenariato_collegamenti

        assert (await partenariato_collegamenti.backfill(db))["errori"] == 0
        percorso = f"/partenariati/call/{g.CALL_GUIDA_ID}/suggeriti"
        stesso_owner = {
            partenariato_accesso.pseudonimo(g.CALL_GUIDA_ID, codice)
            for codice in [g.CODICE_PUBBLICO["Y"], *copie.values()]
        }
        for query in ("", f"?posizione={g.POS_P1}"):
            prima = (await chiama(db, sec, "X", "GET", percorso + query)).json()
            seconda = (await chiama(db, sec, "X", "GET", percorso + query + (
                "&" if query else "?") + "page=2")).json()
            visti = [i["pseudonimo"] for i in prima["items"] + seconda["items"]]
            assert prima["total"] == 3 and len(visti) == 3, query  # 2 dell'owner + T
            assert len(stesso_owner & set(visti)) == 2, query
            senza_canary(json.dumps([prima, seconda]), "Y", "T")
            for company in copie:
                assert company not in json.dumps([prima, seconda])


# ------------------------------------------------------ salva e riepilogo


class TestSalvaERiepilogo:
    async def test_salva_segui_e_rimuovi(self):
        db, sec = await scenario_guida()
        base = f"/partenariati/call/{g.CALL_GUIDA_ID}/salva"
        assert (await chiama(db, sec, "Y", "POST", base)).status_code == 204
        assert (await chiama(db, sec, "Y", "POST", base)).status_code == 204  # idempotente
        salvate = await chiama(db, sec, "Y", "GET", "/partenariati/call?vista=salvate")
        assert [i["id"] for i in salvate.json()["items"]] == [g.CALL_GUIDA_ID]
        assert salvate.json()["items"][0]["salvata"] is True
        riepilogo = await chiama(db, sec, "Y", "GET", "/partenariati/riepilogo")
        assert riepilogo.json() == {"per_te_nuove": 2, "call_attive": 0, "salvate": 1,
                                    "inviti_ricevuti": 0, "candidature_da_decidere": 0,
                                    "messaggi_non_letti": 0}
        [riga] = db.tabelle["partner_call_salvate"]
        assert riga["user_id"] == g.OWNER["Y"]
        assert (await chiama(db, sec, "Y", "DELETE", base)).status_code == 204
        vuote = await chiama(db, sec, "Y", "GET", "/partenariati/call?vista=salvate")
        assert vuote.json()["items"] == []

    async def test_la_propria_call_409_il_membro_403_la_riservata_404(self):
        db, sec = await scenario_guida()
        propria = await chiama(db, sec, "X", "POST", f"/partenariati/call/{g.CALL_GUIDA_ID}/salva")
        membro = await chiama(db, sec, "Y", "POST", f"/partenariati/call/{g.CALL_GUIDA_ID}/salva",
                              active=attiva("Y", editable=False))
        riservata = await chiama(db, sec, "Y", "POST",
                                 f"/partenariati/call/{g.CALL_RISERVATA_ID}/salva")
        malformato = await chiama(db, sec, "Y", "DELETE", "/partenariati/call/non-un-id/salva")
        assert propria.status_code == 409 and propria.json()["error"]["code"] == "call_propria"
        assert membro.status_code == 403
        assert riservata.status_code == 404 and malformato.status_code == 404
        assert db.tabelle.get("partner_call_salvate", []) == []

    async def test_riepilogo_del_creatore(self):
        db, sec = await scenario_guida()
        resp = await chiama(db, sec, "X", "GET", "/partenariati/riepilogo")
        assert resp.json()["call_attive"] == 1

    async def test_riepilogo_senza_azienda(self):
        db, sec = await scenario_guida()
        senza = ActiveCompany(company_id=None, owner_id=g.OWNER["V"], editable=True)
        resp = await chiama(db, sec, "V", "GET", "/partenariati/riepilogo", active=senza)
        assert resp.json() == {"per_te_nuove": 0, "call_attive": 0, "salvate": 0,
                               "inviti_ricevuti": 0, "candidature_da_decidere": 0,
                               "messaggi_non_letti": 0}


# ------------------------------------------------------------ Advisor


class TestAdvisor:
    async def _con_seconda_azienda(self):
        """Il titolare di Y ha una seconda azienda (Y2) con una call pubblicata."""
        db, sec = await scenario_guida()
        riga = copy.deepcopy(db.una("company_profiles", id=g.COMPANY["Y"]))
        riga.update(id=COMPANY_Y2, ragione_sociale="Seconda Sintetica Srl",
                    partita_iva="30000000001")
        db.tabelle["company_profiles"].append(riga)
        call = copy.deepcopy(db.una("partner_calls", id=g.CALL_ALTRA_ID))
        call.update(id=CALL_Y2, company_profile_id=COMPANY_Y2, family_parent_id=g.OWNER["Y"],
                    bando_id=g.BANDO_GUIDA)
        db.tabelle["partner_calls"].append(call)
        from app.services import partenariato_collegamenti

        await partenariato_collegamenti.backfill(db)
        return db, sec

    async def test_le_call_dell_altra_azienda_dello_stesso_owner(self):
        db, sec = await self._con_seconda_azienda()
        bacheca = await chiama(db, sec, "Y", "GET", "/partenariati/call?vista=tutte")
        per_te = await chiama(db, sec, "Y", "GET", "/partenariati/per-te")
        suggeriti = await chiama(db, sec, "Y", "GET", f"/partenariati/call/{CALL_Y2}/suggeriti")
        salva = await chiama(db, sec, "Y", "POST", f"/partenariati/call/{CALL_Y2}/salva")
        assert CALL_Y2 not in bacheca.text and CALL_Y2 not in per_te.text
        assert suggeriti.status_code == 404  # con Y attiva non è il creatore
        assert salva.status_code == 409
        # con Y2 attiva è lui il creatore
        propria = await chiama(db, sec, "Y", "GET", f"/partenariati/call/{CALL_Y2}/suggeriti",
                               active=attiva("Y", company=COMPANY_Y2))
        assert propria.status_code == 200


# ------------------------------------------------------- impostazioni email


class TestEmail:
    async def test_impostazioni_e_disiscrizione(self):
        db, sec = await scenario_guida()
        base = "/me/partenariati/email-settings"
        letto = await chiama(db, sec, "Y", "GET", base)
        assert letto.json() == {"digest_abilitato": True, "eventi_abilitati": True}
        scritto = await chiama(db, sec, "Y", "PUT", base,
                               json={"digest_abilitato": True, "eventi_abilitati": False})
        assert scritto.json() == {"digest_abilitato": True, "eventi_abilitati": False}
        extra = await chiama(db, sec, "Y", "PUT", base, json={
            "digest_abilitato": True, "eventi_abilitati": True, "user_id": g.OWNER["X"]})
        assert extra.status_code == 422
        token = db.una("partner_email_settings", user_id=g.OWNER["Y"])["unsubscribe_token"]
        url = "/partenariati/email/unsubscribe"
        # un tipo sconosciuto: stessa risposta, nessuna impostazione toccata
        await chiama(db, sec, "Y", "PUT", base,
                     json={"digest_abilitato": True, "eventi_abilitati": True})
        resp = await chiama(db, sec, "Y", "POST", url,
                            params={"token": token, "tipo": "sconosciuto"})
        assert resp.status_code == 204
        assert (await chiama(db, sec, "Y", "GET", base)).json() == {
            "digest_abilitato": True, "eventi_abilitati": True}
        await chiama(db, sec, "Y", "PUT", base,
                     json={"digest_abilitato": True, "eventi_abilitati": False})
        resp = await chiama(db, sec, "Y", "POST", url, params={"token": token, "tipo": "digest"})
        assert resp.status_code == 204
        html = await chiama(db, sec, "Y", "POST", url, params={"token": "x", "tipo": "digest"},
                            headers={"accept": "text/html"})
        assert html.status_code == 200 and "Preferenze" in html.text
        ignoto = await chiama(db, sec, "Y", "POST", url, params={
            "token": "00000000-0000-4000-8000-000000000000", "tipo": "eventi"})
        assert ignoto.status_code == 204
        dopo = await chiama(db, sec, "Y", "GET", base)
        assert dopo.json() == {"digest_abilitato": False, "eventi_abilitati": False}

    async def test_il_membro_gestisce_le_proprie_email(self):
        db, sec = await scenario_guida()
        membro = {"id": MEMBRO_X, "role": "cliente", "is_active": True}
        resp = await chiama(db, sec, "X", "PUT", "/me/partenariati/email-settings",
                            user=membro, active=attiva("X", editable=False),
                            json={"digest_abilitato": False, "eventi_abilitati": True})
        assert resp.status_code == 200
        assert db.una("partner_email_settings", user_id=MEMBRO_X)["digest_abilitato"] is False


async def test_la_pagina_di_disiscrizione_escapa_i_parametri():
    pagina = await partenariati_email.unsubscribe_page(token='"><script>x</script>',
                                                       tipo="eventi")
    testo = pagina.body.decode()
    assert "<script>" not in testo and "inviti, candidature e messaggi" in testo
