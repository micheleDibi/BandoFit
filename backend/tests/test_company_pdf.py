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
from app.schemas.company import CompanyOut
from app.schemas.openapi_data import DossierResponse, PersonOut
from app.services import (
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
