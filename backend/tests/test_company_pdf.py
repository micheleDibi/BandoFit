"""Export PDF azienda: costruttori di documento (scheda/dossier) e i due
endpoint di download. Il rendering vero è monkeypatchato (WeasyPrint non è
installato in ambiente di test): si verifica il contratto HTTP e che il payload
grezzo del provider non finisca mai nel documento."""

import httpx
import pytest
from fastapi import FastAPI

from app.api import deps
from app.api.deps import ActiveCompany
from app.api.routers import company
from app.core.errors import register_exception_handlers
from app.schemas.bilanci import BilanciOut, EsercizioOut, IndicatoreOut
from app.schemas.company import CompanyOut
from app.schemas.openapi_data import DossierResponse, PersonOut
from app.services import (
    bilanci_service,
    company_service,
    openapi_service,
    pdf_service,
    preferences_service,
)
from app.services.company_pdf_service import build_dossier_doc, build_scheda_doc

USER = "aaaaaaaa-0000-0000-0000-000000000010"
OWNER = "aaaaaaaa-0000-0000-0000-000000000011"
COMPANY = "cccccccc-0000-0000-0000-000000000012"


def _active(company_id: str | None = COMPANY, editable: bool = True) -> ActiveCompany:
    return ActiveCompany(company_id=company_id, owner_id=OWNER, editable=editable)


def _company(**over) -> CompanyOut:
    base = dict(
        ragione_sociale="Alfa S.r.l.",
        partita_iva="12345678901",
        forma_giuridica="SRL",
        codice_fiscale=None,
        ateco_codice="62.01",
        ateco_descrizione="Produzione software",
        settore_nome="ICT",
        regione_nome="Lombardia",
        classe_dimensionale="piccola",
        fascia_fatturato="2m_10m",
        numero_dipendenti=12,
        comune="Milano",
        beneficiari=[{"id": 9, "nome": "PMI"}],
    )
    base.update(over)
    return CompanyOut(**base)


def _flatten(doc) -> str:
    parts = [doc.title, doc.subtitle or "", *doc.badges, doc.footer or ""]
    for sec in doc.sections:
        parts.append(sec.heading)
        for block in sec.blocks:
            for kv in block.fields:
                parts += [kv.label, kv.value]
            parts += block.headers
            for row in block.rows:
                parts += row
            parts += block.chips
            if block.text:
                parts.append(block.text)
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# Costruttori di documento (puri)
# ---------------------------------------------------------------------------


class TestSchedaDoc:
    def test_struttura_e_label(self):
        doc = build_scheda_doc(_company(), {"regioni": ["Lombardia", "Lazio"]})
        assert doc.title == "Alfa S.r.l."
        headings = [s.heading for s in doc.sections]
        assert headings == [
            "Anagrafica",
            "Attività e dimensione",
            "Sede e contatti",
            "Preferenze di ricerca seguite",
        ]
        testo = _flatten(doc)
        assert "62.01 — Produzione software" in testo  # ateco codice+descrizione
        assert "Piccola impresa" in testo  # classe_dimensionale mappata
        assert "2 – 10 M€" in testo  # fascia_fatturato mappata
        assert "PMI" in testo  # beneficiari come chip
        assert "Lombardia, Lazio" in testo  # preferenze etichettate

    def test_campi_vuoti_omessi(self):
        doc = build_scheda_doc(_company(codice_fiscale=None, telefono=None), {})
        testo = _flatten(doc)
        assert "Codice fiscale" not in testo  # None → riga assente
        # Nessuna sezione preferenze se non ce ne sono.
        assert "Preferenze di ricerca seguite" not in [s.heading for s in doc.sections]

    def test_nessuna_denominazione_ateco_solo_codice(self):
        doc = build_scheda_doc(_company(ateco_descrizione=None), {})
        assert "62.01" in _flatten(doc)


class TestDossierDoc:
    def _resp(self, **over) -> DossierResponse:
        dossier = {
            "anagrafica": {
                "denominazione": "Beta S.p.A.",
                "partita_iva": "99999999999",
                "stato": "Attiva",
                # Chiave inattesa DENTRO una sezione whitelisted: il builder legge
                # solo campi noti via .get(), quindi non deve trapelare.
                "segreto_interno": "LEAK_NESTED",
            },
            "attivita": {
                "ateco": {"codice": "10.1", "descrizione": "Alimentare"},
                "ateco_secondari": ["46.3"],
            },
            "sede": {
                "comune": "Roma",
                "unita_locali": [{"tipo": "Filiale", "comune": "Napoli", "stato": "Attiva"}],
            },
            "bilanci": {"fatturato": 1_500_000},
            "flags": {"startup_innovativa": True, "esportatore": True},
            "partecipazioni": [{"denominazione": "Gamma Srl", "quota": "30%"}],
            # Chiavi NON mappate: NON devono trapelare nel documento.
            "raw": {"SEGRETO": "PAYLOAD_GREZZO"},
            "campo_sconosciuto": "LEAK",
        }
        people = [
            PersonOut(
                kind="manager",
                nome="Mario",
                cognome="Rossi",
                is_legale_rappresentante=True,
                ruoli=[{"description": "Amministratore Unico", "campo_extra": "LEAK_RUOLO"}],
            ),
            PersonOut(kind="shareholder", denominazione="Gamma Srl", quota_percentuale=30.0),
        ]
        base = dict(
            editable=True,
            imported=True,
            fetched_at="2026-07-01T10:00:00+00:00",
            sandbox=True,
            dossier=dossier,
            people=people,
            derived={},
        )
        base.update(over)
        return DossierResponse(**base)

    def test_struttura_badge_footer(self):
        doc = build_dossier_doc(self._resp())
        assert doc.title == "Beta S.p.A."
        assert "Dati di test" in doc.badges and "Attiva" in doc.badges
        headings = [s.heading for s in doc.sections]
        assert "Anagrafica" in headings
        assert "Amministratori e cariche" in headings
        assert "Compagine sociale" in headings
        # Sezioni senza dati omesse.
        assert "Contatti" not in headings
        assert "Organo di controllo" not in headings
        testo = _flatten(doc)
        assert "10.1 — Alimentare" in testo
        assert "Startup innovativa" in testo  # flag → chip con etichetta
        assert "1.500.000 €" in testo  # importo formattato
        assert "Amministratore Unico (Legale rappresentante)" in testo
        assert "30.0%" in testo  # quota socio
        assert "aggiornato al 01/07/2026" in doc.footer

    def test_raw_e_campi_sconosciuti_non_trapelano(self):
        testo = _flatten(build_dossier_doc(self._resp()))
        # Chiavi grezze al livello top del dossier...
        assert "PAYLOAD_GREZZO" not in testo
        assert "LEAK" not in testo
        assert "campo_sconosciuto" not in testo
        # ...e chiavi inattese ANNIDATE in una sezione whitelisted o in un ruolo.
        assert "LEAK_NESTED" not in testo
        assert "LEAK_RUOLO" not in testo

    def test_dossier_minimo_non_esplode(self):
        doc = build_dossier_doc(
            DossierResponse(editable=True, imported=True, dossier={}, people=[], derived={})
        )
        assert doc.title == "Dossier azienda"
        assert doc.sections == []  # tutto vuoto → nessuna sezione


def _bilanci(storico_esito: str | None = "ok") -> BilanciOut:
    """Sei esercizi 2017-2022: il PDF ne mostra solo gli ultimi cinque."""
    esercizi = [
        EsercizioOut(
            anno=anno,
            data_chiusura=f"{anno}-12-31",
            fatturato=1_000_000.0 + anno,
            risultato_esercizio=-25_000.0 if anno == 2020 else 50_000.0,
            dipendenti=12.5,
            fonti={"fatturato": "it_advanced", "risultato_esercizio": "it_advanced",
                   "dipendenti": "it_advanced"},
        )
        for anno in range(2017, 2023)
    ]
    esercizi[-1].patrimonio_netto = 400_000.0
    esercizi[-1].fonti["patrimonio_netto"] = "it_full"
    return BilanciOut(
        editable=True,
        stato="disponibili",
        storico_esito=storico_esito,
        esercizi=esercizi,
        indicatori=[
            IndicatoreOut(chiave="crescita_fatturato_pct", etichetta="Crescita del fatturato",
                          valore=0.1, unita="percentuale", anni=[2021, 2022], formula="f"),
            IndicatoreOut(chiave="copertura_immobilizzazioni",
                          etichetta="Copertura delle immobilizzazioni", valore=None,
                          unita="rapporto", anni=[], formula="f", motivo_mancanza="v1"),
        ],
    )


class TestSezioneBilanci:
    def _resp(self) -> DossierResponse:
        return DossierResponse(
            editable=True, imported=True, people=[], derived={},
            dossier={
                "anagrafica": {"denominazione": "Beta S.p.A."},
                "bilanci": {"fatturato": 1_500_000, "anno": 2021},
            },
        )

    def test_voce_per_ultimi_cinque_esercizi(self):
        doc = build_dossier_doc(self._resp(), _bilanci())
        headings = [s.heading for s in doc.sections]
        # subito dopo «Dati economici»
        assert headings.index("Bilanci per esercizio") == headings.index("Dati economici") + 1
        sezione = doc.sections[headings.index("Bilanci per esercizio")]
        tabella, indicatori, nota = sezione.blocks
        assert tabella.headers == ["Voce", "2018", "2019", "2020", "2021", "2022"]
        voci = [riga[0] for riga in tabella.rows]
        assert voci == [
            "Fatturato", "Utile (perdita) d'esercizio", "Patrimonio netto", "Dipendenti",
        ]  # righe tutte vuote nascoste (es. debiti, EBITDA)
        fatturato = tabella.rows[0]
        assert fatturato[1:] == [
            "1.002.018 €", "1.002.019 €", "1.002.020 €", "1.002.021 €", "1.002.022 €"
        ]
        utile = tabella.rows[1]
        assert utile[3] == "-25.000 €"  # le perdite restano col segno
        pn = tabella.rows[2]
        assert pn[1:] == ["", "", "", "", "400.000 €"]
        assert tabella.rows[3][1] == "12,5"
        # indicatori calcolati: solo quelli con valore
        assert [(kv.label, kv.value) for kv in indicatori.fields] == [
            ("Crescita del fatturato", "0,1 % (2021–2022)")
        ]
        assert "storico bilanci del Registro Imprese" in nota.text
        assert "Registro Imprese, ultimo bilancio depositato" in nota.text
        assert "non completo" not in nota.text
        # «Dati economici» dichiara l'esercizio della fotografia di IT-full
        economici = doc.sections[headings.index("Dati economici")].blocks[0]
        assert ("Esercizio", "2021") in [(kv.label, kv.value) for kv in economici.fields]

    def test_fatturato_di_un_altro_anno_etichettato(self):
        # turnoverYear ≠ anno di chiusura: il fatturato non passa per quello
        # dell'«Esercizio 2021».
        resp = self._resp()
        resp.dossier["bilanci"]["anno_fatturato"] = 2022
        economici = next(s for s in build_dossier_doc(resp).sections
                         if s.heading == "Dati economici").blocks[0]
        etichette = [kv.label for kv in economici.fields]
        assert "Fatturato (2022)" in etichette and "Fatturato" not in etichette
        resp.dossier["bilanci"]["anno_fatturato"] = 2021
        economici = next(s for s in build_dossier_doc(resp).sections
                         if s.heading == "Dati economici").blocks[0]
        assert "Fatturato" in [kv.label for kv in economici.fields]

    def test_storico_incompleto_segnalato(self):
        doc = build_dossier_doc(self._resp(), _bilanci(storico_esito="timeout"))
        sezione = next(s for s in doc.sections if s.heading == "Bilanci per esercizio")
        assert "non è completo" in sezione.blocks[-1].text

    def test_senza_bilanci_nessuna_sezione(self):
        for bilanci in (None, BilanciOut(editable=True, stato="mai_richiesti")):
            doc = build_dossier_doc(self._resp(), bilanci)
            assert "Bilanci per esercizio" not in [s.heading for s in doc.sections]


# ---------------------------------------------------------------------------
# Endpoint di download
# ---------------------------------------------------------------------------


def _make_client() -> httpx.AsyncClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(company.router, prefix="/api/v1")
    app.dependency_overrides[deps.get_current_user] = lambda: {"id": USER}
    app.dependency_overrides[deps.active_company] = lambda: _active()
    app.dependency_overrides[deps.get_primary] = lambda: object()
    app.dependency_overrides[deps.get_secondary] = lambda: object()
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def fake_render(monkeypatch):
    captured: list = []

    def _render(doc):
        captured.append(doc)
        return b"%PDF-1.4 fake-bytes"

    monkeypatch.setattr(pdf_service, "render", _render)
    return captured


class TestSchedaEndpoint:
    async def test_download_ok(self, monkeypatch, fake_render):
        async def fake_get_company(primary, active):
            from app.schemas.company import CompanyResponse

            return CompanyResponse(editable=True, company=_company())

        async def fake_prefs(primary, user_id, active):
            return {}

        monkeypatch.setattr(company_service, "get_company", fake_get_company)
        monkeypatch.setattr(preferences_service, "get_preferences_labeled", fake_prefs)

        async with _make_client() as client:
            resp = await client.get("/api/v1/me/company/export/pdf")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.headers["content-disposition"].startswith('attachment; filename="scheda-')
        assert resp.content == b"%PDF-1.4 fake-bytes"

    async def test_senza_azienda_404(self, monkeypatch, fake_render):
        async def fake_get_company(primary, active):
            from app.schemas.company import CompanyResponse

            return CompanyResponse(editable=True, company=None)

        monkeypatch.setattr(company_service, "get_company", fake_get_company)
        async with _make_client() as client:
            resp = await client.get("/api/v1/me/company/export/pdf")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"

    async def test_motore_pdf_assente_503(self, monkeypatch):
        from app.core.errors import PdfEngineUnavailableError

        async def fake_get_company(primary, active):
            from app.schemas.company import CompanyResponse

            return CompanyResponse(editable=True, company=_company())

        async def fake_prefs(primary, user_id, active):
            return {}

        def _boom(_doc):
            raise PdfEngineUnavailableError()

        monkeypatch.setattr(company_service, "get_company", fake_get_company)
        monkeypatch.setattr(preferences_service, "get_preferences_labeled", fake_prefs)
        monkeypatch.setattr(pdf_service, "render", _boom)
        async with _make_client() as client:
            resp = await client.get("/api/v1/me/company/export/pdf")
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "pdf_unavailable"


class TestDossierEndpoint:
    @pytest.fixture(autouse=True)
    def _senza_bilanci(self, monkeypatch):
        async def fake_get_bilanci(primary, active):
            return BilanciOut(editable=True, stato="mai_richiesti")

        monkeypatch.setattr(bilanci_service, "get_bilanci", fake_get_bilanci)

    async def test_bilanci_nel_pdf_senza_payload_grezzo(self, monkeypatch, fake_render):
        async def fake_get_dossier(primary, active):
            return DossierResponse(
                editable=True, imported=True, people=[], derived={},
                dossier={"anagrafica": {"denominazione": "Beta S.p.A."}},
            )

        async def fake_get_bilanci(primary, active):
            assert active.company_id == COMPANY  # l'azienda ATTIVA
            return _bilanci()

        monkeypatch.setattr(openapi_service, "get_dossier", fake_get_dossier)
        monkeypatch.setattr(bilanci_service, "get_bilanci", fake_get_bilanci)
        async with _make_client() as client:
            resp = await client.get("/api/v1/me/company/dossier/pdf")
        assert resp.status_code == 200
        [doc] = fake_render
        testo = _flatten(doc)
        assert "Bilanci per esercizio" in testo and "1.002.022 €" in testo
        assert "advanced_raw" not in testo and "balanceSheets" not in testo

    async def test_bilanci_non_leggibili_il_pdf_esce_lo_stesso(self, monkeypatch, fake_render):
        async def fake_get_dossier(primary, active):
            return DossierResponse(
                editable=True, imported=True, people=[], derived={},
                dossier={"anagrafica": {"denominazione": "Beta S.p.A."}},
            )

        async def guasto(primary, active):
            raise RuntimeError("db giù")

        monkeypatch.setattr(openapi_service, "get_dossier", fake_get_dossier)
        monkeypatch.setattr(bilanci_service, "get_bilanci", guasto)
        async with _make_client() as client:
            resp = await client.get("/api/v1/me/company/dossier/pdf")
        assert resp.status_code == 200
        [doc] = fake_render
        assert "Bilanci per esercizio" not in [s.heading for s in doc.sections]

    async def test_download_ok_senza_raw(self, monkeypatch, fake_render):
        async def fake_get_dossier(primary, active):
            return DossierResponse(
                editable=True,
                imported=True,
                dossier={
                    "anagrafica": {"denominazione": "Beta S.p.A."},
                    "raw": {"X": "PAYLOAD_GREZZO"},
                },
                people=[],
                derived={},
            )

        monkeypatch.setattr(openapi_service, "get_dossier", fake_get_dossier)
        async with _make_client() as client:
            resp = await client.get("/api/v1/me/company/dossier/pdf")
        assert resp.status_code == 200
        assert resp.headers["content-disposition"].startswith('attachment; filename="dossier-')
        # Il documento passato al renderer non contiene il payload grezzo.
        [doc] = fake_render
        assert "PAYLOAD_GREZZO" not in _flatten(doc)

    async def test_dossier_assente_404(self, monkeypatch, fake_render):
        async def fake_get_dossier(primary, active):
            return DossierResponse(editable=True, imported=False, dossier=None)

        monkeypatch.setattr(openapi_service, "get_dossier", fake_get_dossier)
        async with _make_client() as client:
            resp = await client.get("/api/v1/me/company/dossier/pdf")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"

    async def test_motore_pdf_assente_503(self, monkeypatch):
        from app.core.errors import PdfEngineUnavailableError

        async def fake_get_dossier(primary, active):
            return DossierResponse(
                editable=True,
                imported=True,
                dossier={"anagrafica": {"denominazione": "Beta S.p.A."}},
                people=[],
                derived={},
            )

        def _boom(_doc):
            raise PdfEngineUnavailableError()

        monkeypatch.setattr(openapi_service, "get_dossier", fake_get_dossier)
        monkeypatch.setattr(pdf_service, "render", _boom)
        async with _make_client() as client:
            resp = await client.get("/api/v1/me/company/dossier/pdf")
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "pdf_unavailable"
