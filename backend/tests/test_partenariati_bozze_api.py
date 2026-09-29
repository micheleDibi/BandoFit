"""API delle bozze AI dei documenti del partenariato (WP10): flag spento →
404 anche senza token e con un corpo malformato su ogni rotta nuova; rotte
registrate; forme delle risposte (202 in preparazione, disclaimer fisso
sempre presente); solo chi partecipa alla call avvia e legge, e solo le
proprie bozze; il membro con visibilità legge ma non avvia; limite del piano
0 / N / NULL; PDF vero (ReportLab) con il disclaimer in testa e a piè di
OGNI pagina (senza piè di pagina nessun PDF), nome del file generato dal
server e senza dati vietati (nomi, P.IVA, email, bilanci di altri, contatti
generati dal modello).

Mini-app con il router vero e `dependency_overrides`; dietro, il servizio
vero sul primario finto del WP10 (`FakePrimaryWP10`) caricato con l'esempio
guida. MAI Anthropic: il client AI è finto, i job si eseguono a mano."""

import io

import httpx
import pypdf
import pytest
from fastapi import FastAPI

from app.api import deps
from app.api.deps import ActiveCompany
from app.api.routers import partenariati_bozze
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.schemas.partenariato_bozze import DISCLAIMER
from app.services import partenariato_bozze_service
from app.services import partenariato_candidature_service as candidature
from app.services.partenariato_accesso import carica_call_autorizzata
from app.services.partenariato_bozze_prompts import SezioneBozzaAi
from tests.fixtures.partenariati import esempio_guida as g
from tests.test_partenariato_bozze_service import (  # noqa: F401 — fixture
    CALL,
    VIETATI_SEMPRE,
    FakeAi,
    ambiente_wp10,
    assenti,
    bozza_modello,
    fixture_lavori,
    scenario_xy,
    vietati,
)
from tests.test_partenariato_candidature_service import (  # noqa: F401 — fixture
    MEMBRO_X,
    candida,
    fixture_fondo,
    pseudo,
)
from tests.test_partenariato_indice import (  # noqa: F401 — fixture autouse
    RAGIONE,
    ambiente_wp6,
)

CALL_FINTA = "e0000000-0000-4000-8000-00000000abcd"
BOZZA_FINTA = "90000000-0000-4000-8000-00000000abcd"
BASE = f"/api/v1/partenariati/call/{CALL_FINTA}/bozze"
ROTTE = [
    ("POST", BASE),
    ("GET", BASE),
    ("GET", f"{BASE}/{BOZZA_FINTA}"),
    ("GET", f"{BASE}/{BOZZA_FINTA}/pdf"),
]
B = f"/partenariati/call/{CALL}/bozze"
CHIAVI_BOZZA = {"id", "tipo", "stato", "avviata_at", "conclusa_at", "errore",
                "includi_nome_azienda", "titolo", "sezioni", "note_per_l_utente", "avvisi",
                "disclaimer"}


def utente(nome: str) -> dict:
    return {"id": g.OWNER[nome], "role": "cliente", "is_active": True}


def attiva(nome: str, *, editable: bool = True) -> ActiveCompany:
    return ActiveCompany(company_id=g.COMPANY[nome], owner_id=g.OWNER[nome], editable=editable)


UTENTE_MEMBRO_X = {"id": MEMBRO_X, "role": "cliente", "is_active": True}


def _http(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def mini_app(db, sec, ai, *, user: dict, active: ActiveCompany) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(partenariati_bozze.router, prefix="/api/v1")
    app.dependency_overrides[deps.get_current_user] = lambda: user
    app.dependency_overrides[deps.active_company] = lambda: active
    app.dependency_overrides[deps.get_primary] = lambda: db
    app.dependency_overrides[deps.get_secondary] = lambda: sec
    app.dependency_overrides[deps.get_ai] = lambda: ai
    return app


async def chiama(db, sec, nome: str, metodo: str, percorso: str, *, ai=None, active=None,
                 user=None, **k) -> httpx.Response:
    app = mini_app(db, sec, ai or FakeAi(), user=user or utente(nome),
                   active=active or attiva(nome))
    async with _http(app) as client:
        return await client.request(metodo, f"/api/v1{percorso}", **k)


async def pronta(db, sec, lavori, nome="X", tipo="nda", ai=None, **corpo) -> dict:
    """POST (202) e job eseguito: il JSON della bozza pronta."""
    risposta = await chiama(db, sec, nome, "POST", B, ai=ai,
                            json={"tipo": tipo, **corpo})
    assert risposta.status_code == 202, risposta.text
    assert await lavori.pop() == "pronta"
    letta = await chiama(db, sec, nome, "GET", f"{B}/{risposta.json()['id']}")
    assert letta.status_code == 200 and letta.json()["stato"] == "ready"
    return letta.json()


def estrai_testo_pdf(contenuto: bytes) -> str:
    lettore = pypdf.PdfReader(io.BytesIO(contenuto))
    return " ".join(" ".join(pagina.extract_text() or "" for pagina in lettore.pages).split())


@pytest.fixture
def reportlab(monkeypatch):
    """Il PDF vero, con il motore pure-Python (WeasyPrint non ha le librerie
    di sistema sulla macchina di sviluppo)."""
    monkeypatch.setenv("PDF_ENGINE", "reportlab")
    get_settings.cache_clear()


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

    async def test_rotte_registrate(self):
        from app.main import app

        percorsi = app.openapi()["paths"]
        radice = "/api/v1/partenariati/call/{call_id}/bozze"
        assert set(percorsi[radice]) == {"get", "post"}
        for percorso in (f"{radice}/{{bozza_id}}", f"{radice}/{{bozza_id}}/pdf"):
            assert set(percorsi[percorso]) == {"get"}, percorso


# ------------------------------------------------------------ flusso e forme


class TestFlusso:
    async def test_avvio_poll_e_forme(self, fondo, lavori):
        db, sec = await scenario_xy(fondo)
        avvio = await chiama(db, sec, "X", "POST", B, json={"tipo": "lettera_intenti"})
        assert avvio.status_code == 202, avvio.text
        corpo = avvio.json()
        assert set(corpo) == CHIAVI_BOZZA
        assert (corpo["stato"], corpo["tipo"], corpo["includi_nome_azienda"]) == (
            "pending", "lettera_intenti", False)
        assert corpo["disclaimer"] == DISCLAIMER
        lista = await chiama(db, sec, "X", "GET", B)
        assert lista.status_code == 200
        assert set(lista.json()) == {"bozze", "editable", "disclaimer"}
        assert [b["stato"] for b in lista.json()["bozze"]] == ["pending"]
        assert await lavori.pop() == "pronta"
        letta = await chiama(db, sec, "X", "GET", f"{B}/{corpo['id']}")
        pronta_ = letta.json()
        assert set(pronta_) == CHIAVI_BOZZA and pronta_["stato"] == "ready"
        assert [s["titolo"] for s in pronta_["sezioni"]] == ["Parti", "Obblighi", "Firme"]
        assert pronta_["disclaimer"] == DISCLAIMER
        for testo in (avvio.text, lista.text, letta.text):
            assenti(testo, [*vietati("X", "Y", "T"), "input_snapshot", "esecuzione_id",
                            "cost_cents", "_user_id"])

    async def test_controparte_con_il_proprio_nome(self, fondo, lavori):
        db, sec = await scenario_xy(fondo)
        corpo = await pronta(db, sec, lavori, nome="Y", includi_nome_azienda=True)
        assert corpo["includi_nome_azienda"] is True
        messaggio = db.chiamate("fn_partner_bozza_prenota")[-1]["p_input"]
        assert messaggio["nome_azienda"] == RAGIONE["Y"].upper()
        # le bozze di Y non si vedono da X
        assert (await chiama(db, sec, "X", "GET", B)).json()["bozze"] == []

    @pytest.mark.parametrize(("corpo", "stato"), [
        ({"tipo": "contratto"}, 422),
        ({"tipo": "nda", "altro": 1}, 422),
        ({"tipo": "nda", "includi_nome_azienda": "true"}, 422),
        ({}, 422),
    ])
    async def test_corpo_non_valido(self, fondo, lavori, corpo, stato):
        db, sec = await scenario_xy(fondo)
        risposta = await chiama(db, sec, "X", "POST", B, json=corpo)
        assert risposta.status_code == stato
        assert db.chiamate("fn_partner_bozza_prenota") == []

    async def test_errori_di_prenotazione(self, fondo, lavori):
        db, sec = await scenario_xy(fondo)
        primo = await chiama(db, sec, "X", "POST", B, json={"tipo": "nda"})
        assert primo.status_code == 202
        doppio = await chiama(db, sec, "X", "POST", B, json={"tipo": "nda"})
        assert doppio.status_code == 409
        assert doppio.json()["error"]["code"] == "bozza_in_corso"
        spenta = await chiama(db, sec, "X", "POST", B, ai=FakeAi(enabled=False),
                              json={"tipo": "term_sheet"})
        assert (spenta.status_code, spenta.json()["error"]["code"]) == (503, "ai_not_configured")
        non_pronta = await chiama(db, sec, "X", "GET", f"{B}/{primo.json()['id']}/pdf")
        assert non_pronta.status_code == 409
        assert non_pronta.json()["error"]["code"] == "bozza_non_pronta"
        inesistente = await chiama(db, sec, "X", "GET", f"{B}/{BOZZA_FINTA}")
        assert inesistente.status_code == 404


# ------------------------------------------------------------ accesso


class TestAccesso:
    async def test_solo_partecipanti(self, fondo, lavori):
        db, sec = await scenario_xy(fondo)
        bozza = await pronta(db, sec, lavori)
        for metodo, percorso, k in (
            ("POST", B, {"json": {"tipo": "nda"}}),
            ("GET", B, {}),
            ("GET", f"{B}/{bozza['id']}", {}),
            ("GET", f"{B}/{bozza['id']}/pdf", {}),
        ):
            risposta = await chiama(db, sec, "T", metodo, percorso, **k)
            assert risposta.status_code == 404, (metodo, percorso)
            assert risposta.json()["error"]["code"] == "not_found"
            assenti(risposta.text, vietati("X", "Y"))
        # la controparte non legge le bozze del creatore nemmeno per id
        for suffisso in ("", "/pdf"):
            risposta = await chiama(db, sec, "Y", "GET", f"{B}/{bozza['id']}{suffisso}")
            assert risposta.status_code == 404

    @pytest.mark.parametrize("chi", ["candidato", "invitato", "admin"])
    async def test_chi_legge_la_call_senza_partecipare(self, fondo, lavori, chi):
        """Candidatura o invito ancora in attesa, admin: leggono la call ma
        non partecipano, quindi nessuna bozza (404 su ogni rotta)."""
        db, sec = await scenario_xy(fondo)
        bozza = await pronta(db, sec, lavori)
        user, active = utente("T"), attiva("T")
        if chi == "candidato":
            await candida(db, sec, nome="T")
        elif chi == "invitato":
            await candidature.invita(db, sec, attiva("X"), utente("X"), CALL,
                                     candidature.InvitoIn(pseudonimo=pseudo("T")))
        else:
            user = {**user, "role": "admin"}
        _, ruolo = await carica_call_autorizzata(db, CALL, active, user)
        assert ruolo == chi
        for metodo, percorso, k in (
            ("POST", B, {"json": {"tipo": "nda"}}),
            ("GET", B, {}),
            ("GET", f"{B}/{bozza['id']}", {}),
            ("GET", f"{B}/{bozza['id']}/pdf", {}),
        ):
            risposta = await chiama(db, sec, "T", metodo, percorso, user=user, active=active,
                                    **k)
            assert risposta.status_code == 404, (chi, metodo, percorso)
            assenti(risposta.text, vietati("X", "Y"))
        assert len(db.chiamate("fn_partner_bozza_prenota")) == 1  # solo quella di X

    async def test_membro_in_sola_lettura_non_avvia(self, fondo, lavori, reportlab):
        db, sec = await scenario_xy(fondo)
        bozza = await pronta(db, sec, lavori)
        membro = {"active": attiva("X", editable=False), "user": UTENTE_MEMBRO_X}
        avvio = await chiama(db, sec, "X", "POST", B, json={"tipo": "nda"}, **membro)
        assert avvio.status_code == 403
        assert avvio.json()["error"]["code"] == "forbidden"
        lista = await chiama(db, sec, "X", "GET", B, **membro)
        assert lista.status_code == 200 and lista.json()["editable"] is False
        assert [b["id"] for b in lista.json()["bozze"]] == [bozza["id"]]
        assert (await chiama(db, sec, "X", "GET", f"{B}/{bozza['id']}/pdf",
                             **membro)).status_code == 200
        assert (await chiama(db, sec, "X", "GET", B)).json()["editable"] is True


# ------------------------------------------------------------ limite di piano


class TestLimiteDiPiano:
    async def test_non_incluse(self, fondo, lavori):
        db, sec = await scenario_xy(fondo)
        db.limiti_bozze[g.OWNER["X"]] = 0
        risposta = await chiama(db, sec, "X", "POST", B, json={"tipo": "nda"})
        assert risposta.status_code == 409
        errore = risposta.json()["error"]
        assert errore == {"code": "funzione_non_inclusa",
                          "message": "Il tuo piano non include le bozze dei documenti del "
                                     "partenariato"}
        # il limite è del pool del titolare: Y (un altro owner) sì
        assert (await chiama(db, sec, "Y", "POST", B, json={"tipo": "nda"})).status_code == 202

    async def test_limite_n(self, fondo, lavori):
        db, sec = await scenario_xy(fondo)
        db.limiti_bozze[g.OWNER["X"]] = 2
        await pronta(db, sec, lavori, tipo="nda")
        await pronta(db, sec, lavori, tipo="term_sheet")
        risposta = await chiama(db, sec, "X", "POST", B, json={"tipo": "lettera_intenti"})
        assert risposta.status_code == 409
        assert risposta.json()["error"] == {
            "code": "bozze_esaurite",
            "message": "Hai usato tutte le bozze di documenti di questo mese"}

    async def test_illimitate(self, fondo, lavori):
        db, sec = await scenario_xy(fondo)
        db.limiti_bozze[g.OWNER["X"]] = None
        for tipo in ("nda", "term_sheet", "lettera_intenti", "nda", "nda"):
            await pronta(db, sec, lavori, tipo=tipo)
        assert len((await chiama(db, sec, "X", "GET", B)).json()["bozze"]) == 5


# ------------------------------------------------------------ PDF


class TestPdf:
    async def test_pdf_con_disclaimer_e_senza_dati_vietati(self, fondo, lavori, reportlab):
        db, sec = await scenario_xy(fondo)
        # il modello (finto) prova a scrivere contatti, un nome e segnaposto
        # non previsti: il post-processing li toglie prima del salvataggio
        sporca = bozza_modello(sezioni=[
            SezioneBozzaAi(titolo="Parti", testo=(
                f"Tra [Capofila] e [Partner 1]. Contatti: info@acme.it, +39 333 1234567, "
                f"www.acme.it, P.IVA 12345678903. Referente "
                f"[{RAGIONE['Y']}].")),
            SezioneBozzaAi(titolo="Riservatezza", testo="Le Parti mantengono il segreto."),
        ])
        bozza = await pronta(db, sec, lavori, ai=FakeAi(risposta=sporca))
        assert any("riferimenti non ammessi" in a for a in bozza["avvisi"])
        risposta = await chiama(db, sec, "X", "GET", f"{B}/{bozza['id']}/pdf")
        assert risposta.status_code == 200
        assert risposta.headers["content-type"] == "application/pdf"
        assert risposta.headers["x-content-type-options"] == "nosniff"
        disposizione = risposta.headers["content-disposition"]
        assert disposizione.startswith('attachment; filename="bozza-nda-')
        assert disposizione.endswith('.pdf"') and disposizione.isascii()
        assert risposta.content.startswith(b"%PDF")
        testo = estrai_testo_pdf(risposta.content)
        # disclaimer fisso in testa (sezione «Avvertenza») e a piè di pagina
        assert testo.count(DISCLAIMER) == 2
        assert testo.index("Avvertenza") < testo.index("Parti")
        assert "[Capofila]" in testo and "[Partner 1]" in testo
        assenti(testo, [*vietati("X", "Y", "T"), *VIETATI_SEMPRE, "info@acme.it",
                        "333 1234567", "www.acme.it", "12345678903"])

    async def test_disclaimer_a_pie_di_ogni_pagina(self, fondo, lavori, reportlab):
        """Una bozza di più pagine: il disclaimer è a piè di OGNI pagina (una
        pagina stampata o inoltrata da sola lo porta con sé), non solo
        nell'avvertenza iniziale."""
        db, sec = await scenario_xy(fondo)
        lunga = bozza_modello(sezioni=[
            SezioneBozzaAi(titolo=f"Articolo {i}",
                           testo="Le Parti si impegnano a collaborare. " * 40)
            for i in range(1, 13)])
        bozza = await pronta(db, sec, lavori, tipo="term_sheet", ai=FakeAi(risposta=lunga))
        risposta = await chiama(db, sec, "X", "GET", f"{B}/{bozza['id']}/pdf")
        assert risposta.status_code == 200
        lettore = pypdf.PdfReader(io.BytesIO(risposta.content))
        assert len(lettore.pages) >= 3
        for numero, pagina in enumerate(lettore.pages, 1):
            assert DISCLAIMER in " ".join((pagina.extract_text() or "").split()), numero

    async def test_senza_disclaimer_nessun_pdf(self, fondo, lavori, reportlab, monkeypatch):
        db, sec = await scenario_xy(fondo)
        bozza = await pronta(db, sec, lavori)

        def guasto(contenuto: bytes) -> bytes:
            raise ValueError("pdf illeggibile")

        monkeypatch.setattr(partenariato_bozze_service, "_disclaimer_su_ogni_pagina", guasto)
        risposta = await chiama(db, sec, "X", "GET", f"{B}/{bozza['id']}/pdf")
        assert risposta.status_code == 503
        assert risposta.json()["error"]["code"] == "pdf_unavailable"

    async def test_pdf_col_nome_proprio_solo_su_richiesta(self, fondo, lavori, reportlab):
        db, sec = await scenario_xy(fondo)
        con_nome = bozza_modello(sezioni=[SezioneBozzaAi(
            titolo="Parti", testo=f"Tra {RAGIONE['X'].upper()} e [Partner 1].")])
        bozza = await pronta(db, sec, lavori, ai=FakeAi(risposta=con_nome),
                             includi_nome_azienda=True)
        testo = estrai_testo_pdf((await chiama(db, sec, "X", "GET",
                                        f"{B}/{bozza['id']}/pdf")).content)
        assert RAGIONE["X"].upper() in testo
        assenti(testo, vietati("Y", "T"))
        # senza il flag lo stesso testo del modello perde il nome
        bozza = await pronta(db, sec, lavori, tipo="term_sheet", ai=FakeAi(risposta=con_nome))
        testo = estrai_testo_pdf((await chiama(db, sec, "X", "GET",
                                        f"{B}/{bozza['id']}/pdf")).content)
        assenti(testo, [RAGIONE["X"], RAGIONE["X"].upper()])
        assert DISCLAIMER in testo
